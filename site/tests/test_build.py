import hashlib
import json
import re
import shutil
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin, urlsplit

import pytest

from conftest import html_pages, make_build
from wbot_site.build import SAMPLE_DIR, main
from wbot_site.data import StatsError, percent

OTD_URL = "https://www.soundtransit.org/help-contacts/business-information/open-transit-data-otd/transit-data-terms-use"


class Links(HTMLParser):
    def __init__(self):
        super().__init__()
        self.urls: list[str] = []
        self.ids: set[str] = set()

    def handle_starttag(self, tag, attrs):
        for name, value in attrs:
            if name in ("href", "src") and value:
                self.urls.append(value)
            if name == "id" and value:
                self.ids.add(value)


def url_of(out: Path, page: Path) -> str:
    rel = page.relative_to(out).as_posix()
    return "/" + (rel[: -len("index.html")] if rel.endswith("index.html") else rel)


def target_of(out: Path, path: str) -> Path:
    return out / path.lstrip("/") / "index.html" if path.endswith("/") else out / path.lstrip("/")


def test_sample_builds_every_page(dev_site):
    stats_pages = 1 + 6 + 1 + 33 + 1  # home, routes, stop index, stops, data quality
    text_pages = 6  # about (with AI use), terms, privacy, methodology, data, unavailable
    filtered = 18 + 87  # one per page preset file: 18 system, 87 route
    assert len(html_pages(dev_site)) == stats_pages + text_pages + 1 + filtered  # + 404
    for path in ["routes/901/index.html", "stops/E101/index.html", "data/routes_daily.csv",
                 "static/site.css", "sitemap.xml", "robots.txt", "404.html"]:
        assert (dev_site / path).is_file(), path


def test_every_page_has_the_footer(dev_site):
    for page in html_pages(dev_site):
        html = page.read_text(encoding="utf-8")
        assert html.count('<footer class="site-footer">') == 1, page
        assert "Unofficial. Not affiliated with or endorsed by Intercity Transit." in html, page
        assert "Data provided AS IS by Intercity Transit" in html, page
        assert f'href="{OTD_URL}"' in html, page
        assert "CC BY 4.0" in html and "MIT" in html, page
        for link in ("/about/", "/methodology/", "/data/", "/privacy/", "/terms/", "mailto:contact@wasthebusontime.com"):
            assert f'href="{link}"' in html, (page, link)


def test_no_broken_internal_links(dev_site):
    broken = []
    for page in html_pages(dev_site):
        parser = Links()
        parser.feed(page.read_text(encoding="utf-8"))
        base = url_of(dev_site, page)
        for url in parser.urls:
            parts = urlsplit(urljoin(base, url))
            if parts.scheme or parts.netloc:
                continue  # external or mailto
            if parts.path and parts.path != base and not target_of(dev_site, parts.path).is_file():
                broken.append((base, url))
            if parts.fragment and (not parts.path or parts.path == base) and parts.fragment not in parser.ids:
                broken.append((base, url))
    assert broken == []


def test_dev_pages_have_banners_and_noindex(dev_site):
    for page in html_pages(dev_site):
        html = page.read_text(encoding="utf-8")
        assert '<meta name="robots" content="noindex, nofollow">' in html, page
        assert "DEV PREVIEW: code abc1234, stats sample, built 2026-11-03 12:00 UTC" in html, page
        assert "SAMPLE DATA" in html, page
    assert (dev_site / "robots.txt").read_text() == "User-agent: *\nDisallow: /\n"


SITE_SCRIPTS = ["/static/site.js"]
MAP_SCRIPTS = ["/static/vendor/pmtiles/pmtiles.js", "/static/vendor/protomaps/basemaps.js", "/static/map.js"]


def test_pages_load_only_our_own_scripts(dev_site):
    for page in html_pages(dev_site):
        html = page.read_text(encoding="utf-8")
        # Our optional site script everywhere, the map scripts on /stops/ only; no inline scripts.
        allowed = SITE_SCRIPTS + (MAP_SCRIPTS if page == dev_site / "stops" / "index.html" else [])
        if page == dev_site / "unavailable" / "index.html":
            allowed = []  # self-contained: inline CSS, no scripts
        scripts = re.findall(r"<script[^>]*>", html)
        found = sorted(re.search(r'src="([^"?]+)\?v=[0-9a-f]{10}"', s).group(1) for s in scripts)
        assert found == sorted(allowed), page


def test_static_links_carry_a_content_fingerprint(dev_site, tmp_path):
    html = (dev_site / "index.html").read_text(encoding="utf-8")
    link = re.search(r'href="/static/site.css\?v=([0-9a-f]{10})"', html)
    assert link
    digest = hashlib.sha256((dev_site / "static" / "site.css").read_bytes()).hexdigest()[:10]
    assert link.group(1) == digest


def test_nothing_is_loaded_from_another_host(dev_site):
    loads = re.compile(r'<(?:script|img|iframe)[^>]*\ssrc="([^"]*)"|<link[^>]*rel="stylesheet"[^>]*href="([^"]*)"')
    for page in html_pages(dev_site):
        for src in (a or b for a, b in loads.findall(page.read_text(encoding="utf-8"))):
            assert src.startswith("/") and not src.startswith("//"), (page, src)
    # Our scripts mention other hosts only in the map's attribution links.
    attribution = {"https://www.openstreetmap.org/copyright", "https://protomaps.com"}
    for script in ["map.js", "site.js"]:
        urls = set(re.findall(r"https?://[^\s\"'<>]+", (dev_site / "static" / script).read_text(encoding="utf-8")))
        assert urls <= attribution, (script, urls - attribution)


def test_unavailable_page_loads_nothing_else(dev_site):
    html = (dev_site / "unavailable" / "index.html").read_text(encoding="utf-8")
    assert "<style>" in html
    assert 'rel="stylesheet"' not in html and "<script" not in html and "<img" not in html


def test_every_page_has_the_icon(dev_site):
    for name in ["favicon.ico", "apple-touch-icon.png"]:
        assert (dev_site / name).is_file(), name
    for page in html_pages(dev_site):
        html = page.read_text(encoding="utf-8")
        icons = re.findall(r'<link rel="(?:icon|apple-touch-icon)" href="([^"]+)"', html)
        if page == dev_site / "unavailable" / "index.html":
            # Inline, so the page still loads nothing else.
            assert len(icons) == 1 and icons[0].startswith("data:image/svg+xml;base64,"), page
        else:
            assert [i.split("?")[0] for i in icons] == ["/favicon.ico", "/static/icons/favicon.svg", "/apple-touch-icon.png"], page


def test_scope_switch_on_stats_pages_only(dev_site):
    assert 'id="scope-timepoints"' in (dev_site / "index.html").read_text(encoding="utf-8")
    # A stop page is about one stop: every departure from it, so no switch and no bar.
    for page in [dev_site / "stops" / "E101" / "index.html", dev_site / "about" / "index.html"]:
        html = page.read_text(encoding="utf-8")
        assert 'id="scope-timepoints"' not in html and 'class="settings-bar"' not in html, page


def test_stats_pages_put_the_settings_bar_above_the_headline_and_notes_at_the_end(dev_site):
    for page in [dev_site / "index.html", dev_site / "routes" / "901" / "index.html"]:
        html = page.read_text(encoding="utf-8")
        markers = ['class="notices-line"', 'class="settings-bar"', 'class="headline"', '<caption>', 'id="notes"', 'class="site-footer"']
        order = [html.index(m) for m in markers if m in html]
        assert order == sorted(order), page
        assert ('class="notices-line"' in html) == ('id="notes"' in html), page
        assert html.count('class="settings-bar"') == 1, page
        # One section per scope, all of it below the bar.
        assert html.count('<section class="scope ') == 2, page
        assert html.index('class="settings-bar"') < html.index('<section class="scope '), page
    assert 'id="notes"' in (dev_site / "index.html").read_text(encoding="utf-8")


def stop_tables(html: str, scope: str) -> dict[str, list[str]]:
    """Each stop table in one scope's section of a route page: caption, then the stop codes."""
    section = html.split(f'<section class="scope scope-{scope}">')[1].split("</section>")[0]
    tables = re.findall(r"<caption>((?:Timepoints|Stops) to .*?)</caption>(.*?)</table>", section, re.S)
    return {caption: re.findall(r'<th scope="row"><a href="/stops/(\w+)/">', body) for caption, body in tables}


def test_timepoints_stop_tables_list_timepoints_and_the_last_stop(dev_site):
    route = json.loads((SAMPLE_DIR / "site" / "routes" / "901.json").read_text(encoding="utf-8"))
    html = (dev_site / "routes" / "901" / "index.html").read_text(encoding="utf-8")
    timepoints, all_stops = stop_tables(html, "timepoints"), stop_tables(html, "all_stops")
    assert len(timepoints) == len(all_stops) == len(route["stops"])
    for direction, (caption, codes), (all_caption, all_codes) in zip(route["stops"], timepoints.items(), all_stops.items()):
        stops = direction["stops"]
        assert caption.startswith("Timepoints to ") and caption.endswith(", in route order")
        assert all_caption.startswith("Stops to ")
        # In the sample, each direction's last stop isn't a timepoint, so this checks it stays.
        assert not stops[-1]["timepoint"]
        assert codes == [st["code"] for st in stops if st["timepoint"]] + [stops[-1]["code"]]
        assert all_codes == [st["code"] for st in stops]


def test_stop_pages_say_whether_the_stop_is_a_timepoint(dev_site):
    stop = json.loads((SAMPLE_DIR / "site" / "stops" / "E120.json").read_text(encoding="utf-8"))
    assert stop["scopes"]["timepoints"] is None and stop["stop"]["timepoint_routes"] == []
    page = lambda code: (dev_site / "stops" / code / "index.html").read_text(encoding="utf-8")
    assert "This stop isn't a timepoint." in page("E120")
    assert "This stop is a timepoint: it has times printed" in page("E105")
    # E102 is a timepoint for Route 901 but not for the Saturday shuttle, Route 906.
    assert "This stop is a timepoint for Route 901, but not for Route 906." in page("E102")
    assert page("E102").count('<span class="def">Timepoint</span>') == 1
    assert "(each time a bus left this stop)" in page("E105")
    assert 'class="scope-heading' not in page("E105")


def test_small_route_shows_not_enough_data(dev_site):
    html = (dev_site / "routes" / "906" / "index.html").read_text(encoding="utf-8")
    assert "Not enough data</span> for Route 906 yet" in html


def sample_system() -> dict:
    return json.loads((SAMPLE_DIR / "site" / "system.json").read_text(encoding="utf-8"))


def test_terminal_section_uses_the_headline_window(dev_site):
    t = sample_system()["terminal"]
    html = (dev_site / "index.html").read_text(encoding="utf-8")
    section = html.split("<h3>Terminal departures</h3>")[1].split("<h3>")[0]
    assert f"{percent(t['on_time'], t['n'], 1)}</strong> of {t['n']:,} terminal departures" in section
    assert "Intercity Transit's goal is that 90%" in section and "0 to 5 min late" in section
    assert "about 20 seconds" in section


def test_methodology_compares_with_the_agency_window(dev_site):
    system = sample_system()
    tp, t = system["scopes"]["timepoints"]["summary"], system["terminal"]
    html = (dev_site / "methodology" / "index.html").read_text(encoding="utf-8")
    assert "agency-comparison" not in html
    assert f"{percent(tp['on_time_alt'], tp['n'], 1)} of timepoint departures" in html
    assert f"{percent(t['on_time_alt'], t['n'], 1)} of terminal departures" in html


def test_methodology_gives_the_end_of_line_sources(dev_site):
    eol = sample_system()["end_of_line"]
    html = (dev_site / "methodology" / "index.html").read_text(encoding="utf-8")
    assert "end-of-line-sources" not in html
    assert (f"{percent(eol['sources']['block'], eol['n'], 1)} came from the next trip in the block and "
            f"{percent(eol['sources']['gps'], eol['n'], 1)} from GPS") in html


def test_detours_are_noted(dev_site):
    # The sample's detour: route 902, Oct 20, 2026, 9:00 am to 6:00 pm Pacific, 38 trips.
    route = (dev_site / "routes" / "902" / "index.html").read_text(encoding="utf-8")
    notes = route.split('<h2 id="notes">')[1]
    assert "posted a detour for this route from Oct 20, 2026, 9:00 am to Oct 20, 2026, 6:00 pm Pacific time" in notes
    assert "38 trips" in notes and "Route 902 detour: Sample Ave closed at 4th Ave" in notes
    assert "<details" in notes and "Buses use Placeholder Rd" in notes
    assert "detour" in route.split('<h2 id="notes">')[0].split('class="notices-line"')[1].split("</p>")[0]
    stop = (dev_site / "stops" / "E101" / "index.html").read_text(encoding="utf-8")
    assert 'Route 902 had a posted detour' in stop and 'href="/routes/902/"' in stop
    quality = (dev_site / "data-quality" / "index.html").read_text(encoding="utf-8")
    table = quality.split("<h2>Detours</h2>")[1].split("</table>")[0]
    assert "Route 902" in table and "38" in table and "Sample Ave closed" in table


def test_gap_causes_name_the_agency(dev_site):
    # The sample's 2026-10-28 gap is on the feed's side.
    route = (dev_site / "routes" / "902" / "index.html").read_text(encoding="utf-8")
    assert "a gap in Intercity Transit&#39;s real-time data" in route
    quality = (dev_site / "data-quality" / "index.html").read_text(encoding="utf-8")
    assert "A gap in Intercity Transit&#39;s real-time data" in quality


def test_data_loss_times_are_pacific(dev_site):
    # The sample's first data-loss window starts 2026-10-09T01:00:00Z.
    html = (dev_site / "data-quality" / "index.html").read_text(encoding="utf-8")
    assert "Gaps longer than about 13 minutes (Pacific time)" in html
    table = html.split("<h2>Data-loss windows</h2>")[1].split("</table>")[0]
    assert "Oct 8, 2026, 6:00 pm" in table and "UTC" not in table


def test_prod_refuses_synthetic_stats(tmp_path):
    with pytest.raises(StatsError, match="synthetic"):
        make_build(SAMPLE_DIR, tmp_path / "out", "prod")


def test_prod_refuses_missing_stats(tmp_path):
    (tmp_path / "stats").mkdir()
    with pytest.raises(StatsError, match="no stats"):
        make_build(tmp_path / "stats", tmp_path / "out", "prod")
    with pytest.raises(StatsError, match="no stats"):
        make_build(None, tmp_path / "out", "prod")


def test_prod_exits_nonzero_from_the_command_line(tmp_path, monkeypatch):
    monkeypatch.setenv("WBOT_ENV", "prod")
    with pytest.raises(SystemExit) as exc:
        main(["--stats", str(SAMPLE_DIR), "--out", str(tmp_path / "out")])
    assert exc.value.code == 1


def test_unknown_env_is_refused(tmp_path, monkeypatch):
    monkeypatch.setenv("WBOT_ENV", "staging")
    with pytest.raises(SystemExit) as exc:
        main(["--stats", str(SAMPLE_DIR), "--out", str(tmp_path / "out")])
    assert exc.value.code == 1



def test_dev_label_names_the_preview(tmp_path, monkeypatch):
    # The patterns preview on the dev VM's port 8080 labels its banner.
    monkeypatch.setenv("WBOT_ENV", "dev")
    monkeypatch.setenv("WBOT_DEV_LABEL", "PATTERNS PREVIEW")
    main(["--stats", str(SAMPLE_DIR), "--out", str(tmp_path / "out")])
    html = (tmp_path / "out" / "index.html").read_text(encoding="utf-8")
    assert "PATTERNS PREVIEW: code" in html and "DEV PREVIEW" not in html

def test_dev_falls_back_to_sample(tmp_path):
    (tmp_path / "stats").mkdir()  # a stats repo with no site output yet
    b = make_build(tmp_path / "stats", tmp_path / "out", "dev")
    assert b.stats.synthetic
    assert "SAMPLE DATA" in (tmp_path / "out" / "index.html").read_text(encoding="utf-8")


def test_prod_build_has_no_banner_and_no_noindex(real_looking_stats, tmp_path):
    """Only the unavailable page, the 404 page and filtered pages carry noindex; filtered
    pages let their links be followed."""
    out = tmp_path / "out"
    make_build(real_looking_stats, out, "prod")
    filtered = re.compile(r"(routes/\w+/)?(all-days|weekdays|saturday|sunday)/[a-z-]+/index\.html")
    for page in html_pages(out):
        html = page.read_text(encoding="utf-8")
        assert "DEV PREVIEW" not in html and "SAMPLE DATA" not in html, page
        rel = page.relative_to(out).as_posix()
        robots = re.findall(r'<meta name="robots" content="([^"]*)">', html)
        if rel in ("unavailable/index.html", "404.html"):
            assert robots == ["noindex, nofollow"], page
        elif filtered.fullmatch(rel):
            assert robots == ["noindex, follow"], page
        else:
            assert robots == [], page
    sitemap = (out / "sitemap.xml").read_text(encoding="utf-8")
    assert "<loc>https://wasthebusontime.com/routes/901/</loc>" in sitemap
    assert "unavailable" not in sitemap and "404" not in sitemap
    for slug in ("all-days", "weekdays", "saturday/", "sunday/", "all-day"):
        assert slug not in sitemap, slug
    assert "Sitemap: https://wasthebusontime.com/sitemap.xml" in (out / "robots.txt").read_text()


def test_stats_that_break_the_contract_are_refused(real_looking_stats, tmp_path):
    """The contract's check runs on load (what it checks is tested in test_contract.py)."""
    path = real_looking_stats / "site" / "system.json"
    doc = json.loads(path.read_text(encoding="utf-8"))
    doc["schema"] = 2
    path.write_text(json.dumps(doc), encoding="utf-8")
    with pytest.raises(StatsError, match="schema 2"):
        make_build(real_looking_stats, tmp_path / "out", "prod")


def test_build_is_deterministic(dev_site, tmp_path):
    again = tmp_path / "again"
    make_build(SAMPLE_DIR, again, "dev")
    first = {p.relative_to(dev_site): p.read_bytes() for p in dev_site.rglob("*") if p.is_file()}
    second = {p.relative_to(again): p.read_bytes() for p in again.rglob("*") if p.is_file()}
    assert first == second


def test_output_dir_that_is_not_a_build_is_left_alone(tmp_path):
    out = tmp_path / "precious"
    out.mkdir()
    (out / "notes.txt").write_text("keep me")
    with pytest.raises(SystemExit):
        make_build(SAMPLE_DIR, out, "dev")
    assert (out / "notes.txt").read_text() == "keep me"


def test_tab_titles_name_the_page_first(dev_site):
    titles = {}
    for page in html_pages(dev_site):
        html = page.read_text(encoding="utf-8")
        assert "Was the Bus On Time?</title>" in html, page
        assert '<a class="skip-link" href="#main">' in html, page
        titles[page] = html.split("<title>")[1].split("</title>")[0]
    assert titles[dev_site / "index.html"] == "Was the Bus On Time?"
    assert titles[dev_site / "routes" / "901" / "index.html"].startswith("Route 901 ")
    assert titles[dev_site / "stops" / "E101" / "index.html"].endswith(", stop E101 - Was the Bus On Time?")
    stop_titles = [t for p, t in titles.items() if p.parent.parent.name == "stops"]
    assert len(stop_titles) == len(set(stop_titles))


def test_stop_map_without_javascript_is_the_list(dev_site):
    html = (dev_site / "stops" / "index.html").read_text(encoding="utf-8")
    assert '<section class="stop-map" id="stop-map" hidden' in html
    stops = json.loads((SAMPLE_DIR / "site" / "stops.json").read_text(encoding="utf-8"))["stops"]
    assert html.count('<li><a href="/stops/') == len(stops)


def test_stop_map_data_is_complete(dev_site):
    data = dev_site / "stops" / "data"
    stops = json.loads((data / "stops.json").read_text(encoding="utf-8"))
    assert len(stops) == 33 and all(isinstance(s["lat"], float) and isinstance(s["lon"], float) for s in stops)
    assert json.loads((data / "routes.geojson").read_text(encoding="utf-8"))["type"] == "FeatureCollection"
    presets = sorted(data.glob("map/*/*.json"))
    assert len(presets) == 3 * 4 * 6  # whole period + 2 months, 4 day types, 6 bands
    for path in presets:
        doc = json.loads(path.read_text(encoding="utf-8"))
        assert len(doc["timepoints"]) == len(doc["all_stops"]) == len(stops), path
    html = (dev_site / "stops" / "index.html").read_text(encoding="utf-8")
    assert '<option value="2026-10">Oct 2026</option>' in html


def test_stop_pages_link_to_the_map(dev_site):
    html = (dev_site / "stops" / "E120" / "index.html").read_text(encoding="utf-8")
    assert '<a href="/stops/?stop=E120">Show this stop on the map</a>' in html


def test_tiles_url_reaches_the_page(tmp_path):
    make_build(SAMPLE_DIR, tmp_path / "out", tiles_url="/tiles/olympia.pmtiles")
    assert 'data-tiles="/tiles/olympia.pmtiles"' in (tmp_path / "out" / "stops" / "index.html").read_text(encoding="utf-8")


def test_stats_without_map_data_build_without_the_map(real_looking_stats, tmp_path):
    shutil.rmtree(real_looking_stats / "site" / "map")
    out = tmp_path / "out"
    make_build(real_looking_stats, out, "prod")
    assert 'id="stop-map"' not in (out / "stops" / "index.html").read_text(encoding="utf-8")
    assert "on the map</a>" not in (out / "stops" / "E120" / "index.html").read_text(encoding="utf-8")
    assert not (out / "stops" / "data").exists()


def test_stop_map_days_times_and_routes_are_checkboxes(dev_site):
    html = (dev_site / "stops" / "index.html").read_text(encoding="utf-8")
    for day in ("weekday", "saturday", "sunday"):
        assert f'<input type="checkbox" name="daytype" value="{day}" checked>' in html
    for band in ("early", "am_peak", "midday", "pm_peak", "evening"):
        assert f'<input type="checkbox" name="band" value="{band}" checked>' in html
    for label in ("Early (before 6 am)", "AM peak (6 to 9 am)", "Midday (9 am to 3 pm)", "Evening (6 pm on)"):
        assert f" checked> {label}</label>" in html
    for route in ("901", "902", "903", "904", "905", "906"):
        assert f'<input type="checkbox" name="route" value="{route}" checked>' in html
    for label in ("days", "time of day", "routes"):
        assert f'class="multi-all" checked> All<span class="visually-hidden"> {label}</span>' in html
        assert f'class="multi-reset">Reset<span class="visually-hidden"> {label}</span></button>' in html
    assert '<span class="multi-label">Routes</span>' in html
    assert 'name="daytype"' not in html.split('<details class="multi"')[0]  # no Days dropdown left


@pytest.mark.parametrize("show_alt", [False, True])
def test_alt_window_shows_only_when_switched_on(tmp_path, monkeypatch, show_alt):
    """SHOW_ALT adds the agency's window everywhere it appears, and every table row stays as wide as its header."""
    from wbot_site import windows
    monkeypatch.setattr(windows, "SHOW_ALT", show_alt)
    out = tmp_path / "dist"
    make_build(SAMPLE_DIR, out, "dev")
    route = (out / "routes" / "901" / "index.html").read_text(encoding="utf-8")
    assert ("0 to 5 min late, Intercity Transit's own definition" in route) == show_alt
    assert ("data-alt-window=" in (out / "stops" / "index.html").read_text(encoding="utf-8")) == show_alt
    for page in (out / "routes" / "901" / "index.html", out / "stops" / "E101" / "index.html"):
        html = page.read_text(encoding="utf-8")
        for table in re.findall(r"<table.*?</table>", html, re.S):
            header = re.search(r"<thead>(.*?)</thead>", table, re.S)
            if not header:
                continue
            width = header.group(1).count("<th")
            for row in re.findall(r"<tr>(.*?)</tr>", re.search(r"<tbody>(.*?)</tbody>", table, re.S).group(1), re.S):
                cells = [re.search(r'colspan="(\d+)"', tag) for tag in re.findall(r"<t[hd](?=[\s>])[^>]*>", row)]
                assert sum(int(c.group(1)) if c else 1 for c in cells) == width, (page, row[:120])
