"""Pattern callouts on route and stop pages (spec decisions 7 and 8, ticket 06): at most
MAX_CALLOUTS panels, chosen by the page's filter, and a link line to the Patterns page that
always says what is there. Nothing at all when the stats have no patterns.json."""

import re

import pytest

from test_patterns import carry_over, main_of, no_patterns_site, prev, read  # noqa: F401 (fixture)
from wbot_site import patterns

ROUTES = {"905": "Route 905", "902": "Route 902", "9x": "Route 9X"}
CALL_NAMES = patterns.Names(
    routes=ROUTES,
    headsigns={("905", 0): "Sample Transit Center", ("905", 1): "Uptown", ("902", 0): "Eastside"},
    stops={"E111": "Fictional Blvd & 1st Ave [sb]", "E133": "Sample Station", "E101": "Elm St"},
)


def doc(*ps, too_little=()):
    return {"patterns": list(ps), "too_little_data": list(too_little)}


def general(rank, **over):
    return carry_over(**{"id": f"g{rank}", "rank": rank, **over})


def specific(rank, daytype, band, **over):
    return carry_over(**{"id": f"s{rank}", "rank": rank, "preset": {"daytype": daytype, "band": band}, **over})


def ids(c):
    return [p["id"] for p in c.patterns]


def text(markup):
    return re.sub(r"<[^>]+>", "", str(markup))


# Choosing the panels


def test_never_more_than_two():
    d = doc(general(1), general(2), general(3), specific(4, "weekday", "pm_peak"))
    assert patterns.MAX_CALLOUTS == 2
    assert ids(patterns.route_callouts(d, "905", "all", "all", CALL_NAMES)) == ["g1", "g2"]
    assert ids(patterns.route_callouts(d, "905", "weekday", "pm_peak", CALL_NAMES)) == ["g1", "g2"]


def test_unfiltered_shows_general_patterns_plus_the_strongest_specific_one():
    d = doc(general(1), specific(2, "weekday", "pm_peak"), specific(3, "all", "am_peak"))
    assert ids(patterns.route_callouts(d, "905", "all", "all", CALL_NAMES)) == ["g1", "s2"]
    only_specific = doc(specific(1, "weekday", "pm_peak"), specific(2, "all", "am_peak"))
    assert ids(patterns.route_callouts(only_specific, "905", "all", "all", CALL_NAMES)) == ["s1"]


def test_filtered_shows_general_patterns_plus_those_matching_the_filter():
    d = doc(general(1), specific(2, "weekday", "pm_peak"), specific(3, "all", "am_peak"), specific(4, "saturday", "all"))
    assert ids(patterns.route_callouts(d, "905", "weekday", "am_peak", CALL_NAMES)) == ["g1", "s3"]
    assert ids(patterns.route_callouts(d, "905", "saturday", "midday", CALL_NAMES)) == ["g1", "s4"]
    assert ids(patterns.route_callouts(d, "905", "sunday", "evening", CALL_NAMES)) == ["g1"]


def test_only_the_pages_route():
    d = doc(general(1), general(1, id="other", route="902", direction_id=0, stops=["E133"]))
    assert ids(patterns.route_callouts(d, "905", "all", "all", CALL_NAMES)) == ["g1"]
    assert ids(patterns.route_callouts(d, "902", "all", "all", CALL_NAMES)) == ["other"]


def test_a_kind_without_a_template_is_neither_shown_nor_counted():
    d = doc(general(1), general(2, kind="slow_segment"))
    c = patterns.route_callouts(d, "905", "all", "all", CALL_NAMES)
    assert ids(c) == ["g1"]
    assert text(c.line) == "About patterns, and patterns on other routes"


# The link line on route pages


def test_more_patterns_are_counted_in_the_link():
    d = doc(general(1), general(2), general(3), general(4), general(5))
    line = str(patterns.route_callouts(d, "905", "all", "all", CALL_NAMES).line)
    assert line == '<a href="/patterns/#route-905">3 more patterns for Route 905</a> on the Patterns page'
    one = doc(general(1), general(2), general(3))
    assert text(patterns.route_callouts(one, "905", "all", "all", CALL_NAMES).line) == (
        "1 more pattern for Route 905 on the Patterns page")


def test_unfiltered_counts_the_specific_patterns_left_out():
    d = doc(specific(1, "weekday", "pm_peak"), specific(2, "all", "am_peak"))
    assert text(patterns.route_callouts(d, "905", "all", "all", CALL_NAMES).line) == (
        "1 more pattern for Route 905 on the Patterns page")


def test_filtered_counts_only_that_filter_and_says_so():
    d = doc(general(1), general(2), specific(3, "weekday", "pm_peak"), specific(4, "all", "pm_peak"),
            specific(5, "all", "am_peak"))
    line = str(patterns.route_callouts(d, "905", "weekday", "pm_peak", CALL_NAMES).line)
    assert line == ('<a href="/patterns/#route-905">2 more patterns for Route 905 on weekday PM peaks '
                    '(3 to 6 pm)</a> on the Patterns page')


def test_all_fit():
    d = doc(general(1), specific(2, "weekday", "pm_peak"))
    for dt, band in (("all", "all"), ("weekday", "pm_peak"), ("weekday", "am_peak")):
        line = str(patterns.route_callouts(d, "905", dt, band, CALL_NAMES).line)
        assert line == '<a href="/patterns/">About patterns, and patterns on other routes</a>', (dt, band)


def test_none_found():
    line = str(patterns.route_callouts(doc(general(1, route="902")), "905", "all", "all", CALL_NAMES).line)
    assert line == 'No patterns found for this route yet. <a href="/patterns/">About patterns</a>'


def test_not_enough_data():
    line = str(patterns.route_callouts(doc(too_little=["905"]), "905", "weekday", "pm_peak", CALL_NAMES).line)
    assert line == 'Not enough data for patterns on this route yet. <a href="/patterns/">About patterns</a>'


def test_none_for_this_filter_but_some_for_others():
    d = doc(specific(1, "weekday", "pm_peak"), specific(2, "all", "evening"))
    c = patterns.route_callouts(d, "905", "weekday", "am_peak", CALL_NAMES)
    assert ids(c) == []
    assert str(c.line) == ('No patterns found for this route on weekday AM peaks (6 to 9 am). '
                           '<a href="/patterns/#route-905">2 patterns for Route 905</a> on the Patterns page')


# Stop pages


def stop_doc():
    return doc(
        general(1, id="905-g", stops=["E111"]),
        specific(2, "all", "am_peak", id="905-am", stops=["E111"]),
        specific(3, "weekday", "pm_peak", id="905-pm", stops=["E111"]),
        general(1, id="902-g", route="902", direction_id=0, stops=["E111"], matched=200, late=400, share=0.5),
        general(2, id="905-other-stop", stops=["E133"]),
    )


def test_stop_shows_patterns_starting_there_general_first_largest_first():
    c = patterns.stop_callouts(stop_doc(), "E111", CALL_NAMES)
    assert ids(c) == ["905-g", "902-g"]
    assert ids(patterns.stop_callouts(stop_doc(), "E133", CALL_NAMES)) == ["905-other-stop"]


def test_stop_with_only_specific_patterns_shows_the_strongest():
    d = doc(specific(1, "all", "am_peak", id="a", matched=100, late=200, share=0.5),
            specific(2, "weekday", "pm_peak", id="b", matched=300, late=600, share=0.5))
    assert ids(patterns.stop_callouts(d, "E111", CALL_NAMES)) == ["b"]


def test_stop_links_once_per_route_with_its_count():
    line = str(patterns.stop_callouts(stop_doc(), "E111", CALL_NAMES).line)
    assert line == ('On the Patterns page: <a href="/patterns/#route-905">2 more patterns for Route 905</a> '
                    'and <a href="/patterns/#route-902">patterns for Route 902</a>')


def test_stop_counts_only_patterns_at_that_stop():
    line = str(patterns.stop_callouts(stop_doc(), "E133", CALL_NAMES).line)
    assert line == 'On the Patterns page: <a href="/patterns/#route-905">patterns for Route 905</a>'


def test_stop_without_patterns():
    line = str(patterns.stop_callouts(stop_doc(), "E101", CALL_NAMES).line)
    assert line == 'No patterns found at this stop yet. <a href="/patterns/">About patterns</a>'


def test_a_page_does_not_link_to_itself():
    d = doc(general(1, previous=prev(("905", 1, 510))))
    route = patterns.route_callouts(d, "905", "all", "all", CALL_NAMES).panels[0]["sentence"]
    assert 'href="/routes/905/"' not in str(route) and "from Route 905, going the other way," in str(route)
    assert 'href="/stops/E111/"' in str(route)
    stop = patterns.stop_callouts(d, "E111", CALL_NAMES).panels[0]["sentence"]
    assert 'href="/stops/E111/"' not in str(stop) and 'href="/routes/905/"' in str(stop)


def test_link_lines_use_no_banned_words():
    d = stop_doc()
    lines = [patterns.route_callouts(d, s, dt, b, CALL_NAMES).line
             for s in ("905", "902", "9x") for dt, b in (("all", "all"), ("weekday", "pm_peak"), ("sunday", "midday"))]
    lines += [patterns.stop_callouts(d, code, CALL_NAMES).line for code in ("E111", "E133", "E101")]
    lines.append(patterns.route_callouts(doc(too_little=["905"]), "905", "all", "all", CALL_NAMES).line)
    for line in lines:
        assert patterns.banned_words(text(line)) == [], line


# Built pages (sample stats: 905 has two general patterns and one for AM peaks; 902 two for
# PM peaks; 901 none; 906 too little data)


def callouts_of(html, scope="timepoints"):
    """The callouts in a page's main content (a route page's given scope)."""
    main = main_of(html)
    if 'class="scope scope-' in main:
        main = main.split(f'<section class="scope scope-{scope}">', 1)[1].split("</section>", 1)[0]
    found = re.findall(r'<div class="pattern-callouts">.*?<p class="pattern-more">.*?</p>\n</div>', main, re.S)
    assert len(found) == 1
    return found[0]


def panels(block):
    return block.count('<div class="pattern">')


def more_line(block):
    return text(re.search(r'<p class="pattern-more">(.*?)</p>', block, re.S).group(1))


@pytest.mark.parametrize("path, n, line", [
    ("routes/905", 2, "1 more pattern for Route 905 on the Patterns page"),
    ("routes/905/weekdays/am-peak", 2, "1 more pattern for Route 905 on weekday AM peaks (6 to 9 am) on the Patterns page"),
    ("routes/905/weekdays/pm-peak", 2, "About patterns, and patterns on other routes"),
    ("routes/902", 1, "1 more pattern for Route 902 on the Patterns page"),
    ("routes/902/weekdays/pm-peak", 2, "About patterns, and patterns on other routes"),
    ("routes/902/weekdays/am-peak", 0, "No patterns found for this route on weekday AM peaks (6 to 9 am). "
                                       "2 patterns for Route 902 on the Patterns page"),
    ("routes/901", 0, "No patterns found for this route yet. About patterns"),
    ("routes/906", 0, "Not enough data for patterns on this route yet. About patterns"),
    ("stops/E133", 2, "On the Patterns page: patterns for Route 902 and patterns for Route 905"),
    ("stops/E111", 2, "On the Patterns page: patterns for Route 905"),
    ("stops/E120", 0, "No patterns found at this stop yet. About patterns"),
])
def test_built_pages(dev_site, path, n, line):
    block = callouts_of(read(dev_site, path))
    assert panels(block) == n
    assert more_line(block) == line


def test_route_page_has_the_same_callouts_in_both_scopes(dev_site):
    html = read(dev_site, "routes/905")
    assert callouts_of(html, "timepoints") == callouts_of(html, "all_stops")


def test_callouts_come_after_the_headline_numbers(dev_site):
    route = main_of(read(dev_site, "routes/905")).split('<section class="scope scope-timepoints">', 1)[1]
    assert route.index('class="headline"') < route.index('class="chart') < route.index('class="pattern-callouts"')
    assert route.index('class="pattern-callouts"') < route.index("<caption>By direction</caption>")
    stop = main_of(read(dev_site, "stops/E133"))
    assert stop.index('class="headline"') < stop.index('class="pattern-callouts"') < stop.index("<h2>Details</h2>")


def test_the_panels_are_the_pattern_panel(dev_site):
    block = callouts_of(read(dev_site, "stops/E133"))
    assert '<p class="pattern-label">Pattern</p>' in block
    assert '<p class="pattern-note">A pattern in past trips, not a measured cause.</p>' in block
    assert '<a href="/patterns/#how-we-find-patterns">How we find patterns</a>' in block


def test_no_patterns_json_no_callouts(no_patterns_site):
    for path in ("routes/905", "routes/905/weekdays/am-peak", "stops/E133", "stops/E120"):
        html = read(no_patterns_site, path)
        assert "pattern-callouts" not in html and "pattern-more" not in html, path


def test_callouts_add_no_files(dev_site, no_patterns_site):
    files = lambda out: {p.relative_to(out).as_posix() for p in out.rglob("*") if p.is_file()}
    # Only the Patterns page and its download, from ticket 05.
    assert files(dev_site) - files(no_patterns_site) == {"patterns/index.html", "data/patterns.csv"}
    assert files(no_patterns_site) <= files(dev_site)
