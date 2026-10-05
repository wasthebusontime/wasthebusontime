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
from wbot_site.data import StatsError

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
    assert len(html_pages(dev_site)) == stats_pages + text_pages + 1  # + 404
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


def test_toggle_on_stats_pages_only(dev_site):
    assert 'id="scope-timepoints"' in (dev_site / "index.html").read_text(encoding="utf-8")
    assert 'id="scope-timepoints"' in (dev_site / "stops" / "E101" / "index.html").read_text(encoding="utf-8")
    assert 'id="scope-timepoints"' not in (dev_site / "about" / "index.html").read_text(encoding="utf-8")


def test_stats_pages_put_the_toggle_under_the_headline_and_notes_at_the_end(dev_site):
    for page in [dev_site / "index.html", dev_site / "routes" / "901" / "index.html", dev_site / "stops" / "E101" / "index.html"]:
        html = page.read_text(encoding="utf-8")
        markers = ['class="notices-line"', 'class="headline"', 'class="scope-toggle"', '<caption>', 'id="notes"', 'class="site-footer"']
        order = [html.index(m) for m in markers if m in html]
        assert order == sorted(order), page
        assert ('class="notices-line"' in html) == ('id="notes"' in html), page
        assert html.count('class="scope-toggle"') == 1, page
    assert 'id="notes"' in (dev_site / "index.html").read_text(encoding="utf-8")


def test_never_timepoint_stop_says_so(dev_site):
    stop = json.loads((SAMPLE_DIR / "site" / "stops" / "E120.json").read_text(encoding="utf-8"))
    assert stop["scopes"]["timepoints"] is None
    assert "This stop isn't a timepoint" in (dev_site / "stops" / "E120" / "index.html").read_text(encoding="utf-8")


def test_small_route_shows_not_enough_data(dev_site):
    html = (dev_site / "routes" / "906" / "index.html").read_text(encoding="utf-8")
    assert "Not enough data</span> for Route 906 yet" in html


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


def test_dev_falls_back_to_sample(tmp_path):
    (tmp_path / "stats").mkdir()  # a stats repo with no site output yet
    b = make_build(tmp_path / "stats", tmp_path / "out", "dev")
    assert b.stats.synthetic
    assert "SAMPLE DATA" in (tmp_path / "out" / "index.html").read_text(encoding="utf-8")


def test_prod_build_has_no_banner_and_no_noindex(real_looking_stats, tmp_path):
    out = tmp_path / "out"
    make_build(real_looking_stats, out, "prod")
    for page in html_pages(out):
        html = page.read_text(encoding="utf-8")
        assert "DEV PREVIEW" not in html and "SAMPLE DATA" not in html, page
        noindex = page.relative_to(out).as_posix() in ("unavailable/index.html", "404.html")
        assert ('content="noindex' in html) == noindex, page
    sitemap = (out / "sitemap.xml").read_text(encoding="utf-8")
    assert "<loc>https://wasthebusontime.com/routes/901/</loc>" in sitemap
    assert "unavailable" not in sitemap and "404" not in sitemap
    assert "Sitemap: https://wasthebusontime.com/sitemap.xml" in (out / "robots.txt").read_text()


def test_unknown_schema_is_refused(real_looking_stats, tmp_path):
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


def test_tab_titles_start_with_our_name(dev_site):
    for page in html_pages(dev_site):
        html = page.read_text(encoding="utf-8")
        assert "<title>WBOT - " in html, page
    assert "<title>WBOT - Was the Bus On Time</title>" in (dev_site / "index.html").read_text(encoding="utf-8")


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


def test_map_preset_that_misses_stops_is_refused(real_looking_stats, tmp_path):
    path = real_looking_stats / "site" / "map" / "all" / "weekday-midday.json"
    doc = json.loads(path.read_text(encoding="utf-8"))
    doc["all_stops"].pop()
    path.write_text(json.dumps(doc), encoding="utf-8")
    with pytest.raises(StatsError, match="weekday-midday.json: 32 stops"):
        make_build(real_looking_stats, tmp_path / "out", "prod")


def test_stop_map_days_times_and_routes_are_checkboxes(dev_site):
    html = (dev_site / "stops" / "index.html").read_text(encoding="utf-8")
    for day in ("weekday", "saturday", "sunday"):
        assert f'<input type="checkbox" name="daytype" value="{day}" checked>' in html
    for band in ("early", "am_peak", "midday", "pm_peak", "evening"):
        assert f'<input type="checkbox" name="band" value="{band}" checked>' in html
    for route in ("901", "902", "903", "904", "905", "906"):
        assert f'<input type="checkbox" name="route" value="{route}" checked>' in html
    assert html.count('<input type="checkbox" class="multi-all" checked> All') == 3
    assert html.count('<button type="button" class="multi-reset">Reset</button>') == 3
    assert '<span class="multi-label">Routes</span>' in html
    assert 'name="daytype"' not in html.split('<details class="multi"')[0]  # no Days dropdown left
