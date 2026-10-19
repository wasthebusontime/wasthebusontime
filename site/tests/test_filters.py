"""Day type and time of day filters: URLs, which pages exist, menus, links, wording."""

import json
import re
import shutil

import pytest

from conftest import html_pages, make_build
from wbot_site import build, contract, filters
from wbot_site.build import SAMPLE_DIR
from wbot_site.data import StatsError, percent

PRESETS = SAMPLE_DIR / "site" / "presets"
FILTERED = re.compile(r"(routes/\w+/)?(all-days|weekdays|saturday|sunday)/[a-z-]+/")


def page(out, path: str) -> str:
    return (out / path / "index.html").read_text(encoding="utf-8")


def menu(html: str, label: str) -> list[tuple[str, str | None, bool]]:
    """(text, href, current) of each choice in a settings bar dropdown; href None when grayed out."""
    block = html.split(f'<span class="dropdown-label">{label}</span>')[1].split("</details>")[0]
    items = []
    for li in re.findall(r"<li>(.*?)</li>", block):
        if m := re.match(r'<a href="([^"]+)"( aria-current="page")?>(.*)</a>', li):
            items.append((m.group(3), m.group(1), bool(m.group(2))))
        else:
            assert 'class="unavailable" aria-disabled="true"' in li, li
            items.append((re.sub(r"<[^>]+>", "", li), None, False))
    return items


def test_filter_paths_always_have_both_parts():
    paths = {(dt, b): filters.path(dt, b) for dt in contract.MAP_DAYTYPES for b in contract.MAP_BANDS}
    assert paths.pop(("all", "all")) == ""
    assert paths[("weekday", "am_peak")] == "weekdays/am-peak/"
    assert paths[("all", "evening")] == "all-days/evening/"
    assert paths[("sunday", "all")] == "sunday/all-day/"
    assert paths[("saturday", "pm_peak")] == "saturday/pm-peak/"
    assert len(set(paths.values())) == len(contract.PRESETS) == 23
    assert all(FILTERED.fullmatch(p) for p in paths.values())


def test_filter_names_itself():
    f = filters.make("/routes/13/", "weekday", "am_peak", lambda dt, b: True)
    assert (f.words, f.title, f.lead) == ("weekdays, AM peak", "Weekdays, AM peak", "Weekdays, AM peak (6 to 9 am)")
    f = filters.make("/", "sunday", "all", lambda dt, b: True)
    assert (f.words, f.lead) == ("Sundays, all day", "Sundays, all day")
    f = filters.make("/", "all", "early", lambda dt, b: True)
    assert (f.words, f.lead) == ("all days, early", "All days, early (before 6 am)")
    assert not filters.make("/", "all", "all", lambda dt, b: True).active


def test_a_filtered_page_exists_exactly_when_its_preset_file_does(dev_site):
    expected = set()
    for folder in PRESETS.iterdir():
        part = filters.path(*folder.name.split("-"))
        expected.add(part)
        expected |= {f"routes/{f.stem}/{part}" for f in (folder / "routes").glob("*.json")}
    built = {page.parent.relative_to(dev_site).as_posix() + "/" for page in html_pages(dev_site)}
    assert {p for p in built if FILTERED.fullmatch(p)} == expected
    # Route 905 has no Sunday service, Route 906 runs only middays and Saturdays, and the
    # sample has no early service at all.
    assert not (dev_site / "routes" / "905" / "sunday").exists()
    assert not (dev_site / "routes" / "906" / "weekdays").exists()
    assert not (dev_site / "routes" / "906" / "saturday" / "am-peak").exists()
    assert not any(dev_site.glob("**/early"))


def test_menus_mark_the_current_choice_and_gray_out_choices_with_no_service(dev_site):
    html = page(dev_site, "routes/905")
    assert menu(html, "Day type") == [
        ("All days", "/routes/905/", True),
        ("Weekdays", "/routes/905/weekdays/all-day/", False),
        ("Saturday", "/routes/905/saturday/all-day/", False),
        ("Sunday (no service)", None, False),
    ]
    assert menu(html, "Time of day") == [
        ("All day", "/routes/905/", True),
        ("Early (before 6 am) (no service)", None, False),
        ("AM peak (6 to 9 am)", "/routes/905/all-days/am-peak/", False),
        ("Midday (9 am to 3 pm)", "/routes/905/all-days/midday/", False),
        ("PM peak (3 to 6 pm)", "/routes/905/all-days/pm-peak/", False),
        ("Evening (6 pm on)", "/routes/905/all-days/evening/", False),
    ]
    # A choice keeps the other menu's choice: Saturday AM peak has no Route 905 service.
    html = page(dev_site, "routes/905/weekdays/am-peak")
    assert menu(html, "Day type") == [
        ("All days", "/routes/905/all-days/am-peak/", False),
        ("Weekdays", "/routes/905/weekdays/am-peak/", True),
        ("Saturday (no service)", None, False),
        ("Sunday (no service)", None, False),
    ]
    assert ("All day", "/routes/905/weekdays/all-day/", False) in menu(html, "Time of day")
    # Every page of the home page and the routes has both menus; stop pages have none.
    for p in html_pages(dev_site):
        if p.name != "index.html":
            continue
        rel = p.parent.relative_to(dev_site).as_posix() + "/"
        has = 'class="dropdown"' in p.read_text(encoding="utf-8")
        assert has == (rel == "./" or FILTERED.fullmatch(rel) is not None or re.fullmatch(r"routes/\w+/", rel) is not None), p


def test_filtered_home_page_keeps_every_route_and_links_to_the_same_filter(dev_site):
    html = page(dev_site, "sunday/all-day")
    system = json.loads((SAMPLE_DIR / "site" / "system.json").read_text(encoding="utf-8"))
    section = html.split('<section class="scope scope-timepoints">')[1].split("<caption>Routes</caption>")[1].split("</table>")[0]
    rows = re.findall(r"<tr( class=\"no-service\")?><th scope=\"row\"[^>]*>(.*?)</th>(.*?)</tr>", section, re.S)
    assert len(rows) == len(system["routes"])
    for (gray, head, cells), route in zip(rows, system["routes"]):
        slug = route["slug"]
        if slug in ("905", "906"):
            assert gray and "<a " not in head and f"Route {slug}" in head and "No service" in cells
        else:
            assert not gray and f'<a href="/routes/{slug}/sunday/all-day/">Route {slug}</a>' in head
    # The site's own navigation goes back to the unfiltered pages.
    nav = html.split('<nav class="site-nav"')[1].split("</nav>")[0]
    assert '<a href="/">Routes</a>' in nav and "sunday" not in nav


def test_filtered_pages_name_the_filter_and_show_the_preset(dev_site):
    html = page(dev_site, "routes/901/weekdays/am-peak")
    assert "<title>Route 901, weekdays, AM peak - Was the Bus On Time?</title>" in html
    preset = json.loads((PRESETS / "weekday-am_peak" / "routes" / "901.json").read_text(encoding="utf-8"))
    s = preset["scopes"]["timepoints"]["summary"]
    assert ('<p class="headline-main"><span class="headline-lead">Weekdays, AM peak (6 to 9 am):</span> '
            f'<span class="big">{percent(s["on_time"], s["n"])}</span>') in html
    assert f"out of {s['n']:,} departures" in html
    home = page(dev_site, "all-days/evening")
    assert "<title>All days, evening - Was the Bus On Time?</title>" in home
    assert '<span class="headline-lead">All days, evening (6 pm on):</span>' in home
    # Unfiltered pages have no lead.
    assert "headline-lead" not in page(dev_site, "routes/901") and "headline-lead" not in page(dev_site, "")


def test_trips_completeness_and_notes_are_never_filtered(dev_site):
    for base, filtered, marker in [
        ("routes/902", "routes/902/saturday/all-day", "<h3>Trips</h3>"),
        ("", "weekdays/pm-peak", "<h2>How complete the data is</h2>"),
    ]:
        a, b = page(dev_site, base), page(dev_site, filtered)
        assert a.split(marker)[1] == b.split(marker)[1], filtered
        assert a.split('class="notices-line"')[1].split("</p>")[0] == b.split('class="notices-line"')[1].split("</p>")[0]


def test_filtered_charts_mark_the_choice(dev_site):
    html = page(dev_site, "routes/901/weekdays/am-peak")
    section = html.split('<section class="scope scope-timepoints">')[1].split("</section>")[0]
    charts = dict(re.findall(r'<h3 class="chart-title">(.*?)</h3>(.*?)</figure>', section, re.S))
    assert '<title>Weekday (chosen)</title>' in charts["By day type"]
    assert '<title>AM peak (6 to 9 am)</title>' in charts["By hour of the day"]
    assert "Shaded: AM peak (6 to 9 am), the time of day chosen." in charts["By hour of the day"]
    assert "Saturday</li>" not in charts["Each day"] and 'class="saturday"' not in charts["Each day"]
    assert "on time each weekday over the last" in charts["Each day"]
    # Unfiltered: no marks.
    assert 'class="chosen"' not in page(dev_site, "routes/901")


def test_stats_without_presets_build_without_filters(real_looking_stats, tmp_path):
    shutil.rmtree(real_looking_stats / "site" / "presets")
    out = tmp_path / "out"
    make_build(real_looking_stats, out, "prod")
    assert len(html_pages(out)) == 49
    for path in ("", "routes/901"):
        html = page(out, path)
        assert 'class="settings-bar"' in html and 'class="dropdown"' not in html
        assert "<strong>Day type</strong>" not in html
    assert not (out / "weekdays").exists()


def test_the_build_fails_well_before_the_file_cap(tmp_path, monkeypatch):
    assert build.MAX_FILES <= build.FILE_CAP * 3 // 4
    out = tmp_path / "out"
    make_build(SAMPLE_DIR, out)
    files = sum(1 for p in out.rglob("*") if p.is_file())
    assert files < build.MAX_FILES
    monkeypatch.setattr(build, "MAX_FILES", files - 1)
    with pytest.raises(StatsError, match=f"the build has {files:,} files.*Cloudflare Pages allows 20,000"):
        make_build(SAMPLE_DIR, tmp_path / "again")
