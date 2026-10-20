"""Builds the static site from the published statistics.

    python -m wbot_site.build --stats <stats dir> --out <output dir>

WBOT_ENV=dev (the default) adds the preview banner and noindex; WBOT_ENV=prod has
neither and refuses synthetic or missing stats. In dev, missing stats fall back to
the committed sample with a "SAMPLE DATA" banner. WBOT_DEV_LABEL names the preview
in the banner (default "DEV PREVIEW"; the patterns preview on :8080 sets its own). WBOT_TILES_URL is the basemap
tile file for the stop map; without it the map has no basemap.
"""

import argparse
import base64
import hashlib
import json
import logging
import os
import shutil
import sys
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import markdown
from jinja2 import Environment, PackageLoader, StrictUndefined

from wbot_site import agency, charts, contract, data, filters, patterns, windows
from wbot_site.data import Stats, StatsError

log = logging.getLogger("wbot_site")

SITE_DIR = Path(__file__).resolve().parents[2]
CONTENT_DIR = SITE_DIR / "content"
SAMPLE_DIR = SITE_DIR / "sample-stats"
STATIC_DIR = Path(__file__).resolve().parent / "static"
ROOT_ICONS = ("favicon.ico", "apple-touch-icon.png")
BASE_URL = "https://wasthebusontime.com"
ENVS = ("dev", "prod")

# Markdown file in content/ -> URL path. Pages listed in NO_SITEMAP carry noindex.
PAGES = {
    "about": "about/",
    "terms": "terms/",
    "privacy": "privacy/",
    "methodology": "methodology/",
    "data": "data/",
    "unavailable": "unavailable/",
}
NO_SITEMAP = {"unavailable/", "404.html"}
# Cloudflare Pages (Free plan) takes at most 20,000 files per site. The build fails well
# before that, so a growing site is noticed while the last good build is still up.
FILE_CAP = 20_000
MAX_FILES = 15_000
# Markers in content pages replaced by a macro (macros.html) over the system's numbers: the
# numbers on the agency's own window, and where end-of-line arrivals came from.
STATS_BLOCKS = {"<!-- agency-comparison -->": "agency_comparison", "<!-- end-of-line-sources -->": "end_of_line_sources"}
# Markers replaced by a macro only when the stats have patterns.json, and removed otherwise.
PATTERN_BLOCKS = {"<!-- patterns-method -->": "patterns_method", "<!-- patterns-files -->": "patterns_files"}
PATTERNS_PATH = "patterns/"


@dataclass
class Build:
    out: Path
    env: str
    stats: Stats
    jinja: Environment
    pages: list[str] = field(default_factory=list)
    filtered: set[str] = field(default_factory=set)

    def render(self, template: str, path: str, *, filtered: bool = False, **context) -> None:
        """Renders a template to `path` ('' is the home page, 'x/' is x/index.html). Filtered
        pages stay out of search results, but their links are followed."""
        target = self.out / path / "index.html" if path == "" or path.endswith("/") else self.out / path
        target.parent.mkdir(parents=True, exist_ok=True)
        if self.env == "dev" or path in NO_SITEMAP:
            robots = "noindex, nofollow"
        else:
            robots = "noindex, follow" if filtered else ""
        html = self.jinja.get_template(template).render(path="/" + path, robots=robots, **context)
        target.write_text(html, encoding="utf-8", newline="\n")
        self.pages.append(path)
        if filtered:
            self.filtered.add(path)


def choose_stats(stats_dir: Path | None, env: str) -> Stats:
    if stats_dir is None or not data.has_stats(stats_dir):
        if env == "prod":
            raise StatsError(f"no stats found in {stats_dir}; a prod build needs the published stats")
        log.warning("no stats in %s; using the sample data in %s", stats_dir, SAMPLE_DIR)
        stats_dir = SAMPLE_DIR
    stats = data.load_stats(stats_dir)
    if stats.synthetic and env == "prod":
        raise StatsError(f"{stats_dir} holds synthetic sample data; a prod build refuses it")
    return stats


def asset_url(path: str) -> str:
    """/static/site.css -> /static/site.css?v=<content hash>, so browsers never keep a stale copy."""
    digest = hashlib.sha256((STATIC_DIR / path.removeprefix("/static/")).read_bytes()).hexdigest()[:10]
    return f"{path}?v={digest}"


def inline_icon() -> str:
    """The SVG icon as a data: URL, for the page that may load nothing else (/unavailable/)."""
    return "data:image/svg+xml;base64," + base64.b64encode((STATIC_DIR / "icons" / "favicon.svg").read_bytes()).decode()


def make_jinja(env: str, stats: Stats, banner: dict) -> Environment:
    jinja = Environment(
        loader=PackageLoader("wbot_site"),
        autoescape=True,
        undefined=StrictUndefined,
        trim_blocks=True,
        lstrip_blocks=True,
        keep_trailing_newline=True,
    )
    jinja.globals.update(
        env=env,
        charts=charts,
        meta=stats.meta,
        synthetic=stats.synthetic,
        banner=banner,
        inline_icon=inline_icon,
        has_patterns=stats.patterns is not None,
        min_sample=stats.meta["min_sample"],
        period=data.period_text(stats.meta),
        scopes=contract.SCOPES,
        scope_labels=data.SCOPE_LABELS,
        daytypes=contract.DAYTYPES,
        daytype_labels=data.DAYTYPE_LABELS,
        windows=stats.windows,
        agency=agency,
        enough=data.enough,
        split=data.split,
        count_key=contract.count_key,
        percent=data.percent,
        fraction=data.fraction,
        alert_details=data.alert_details,
    )
    jinja.filters.update(
        asset=asset_url,
        number=data.number,
        delay=data.delay_text,
        date=data.date_text,
        month=data.month_text,
        hour=data.hour_text,
        timestamp=data.timestamp_text,
        headline=data.alert_headline,
        directions=data.directions_html,
    )
    return jinja


def build(
    stats_dir: Path | None,
    out: Path,
    env: str = "dev",
    *,
    code_commit: str = "unknown",
    stats_commit: str = "none",
    build_time: datetime | None = None,
    tiles_url: str = "",
    dev_label: str = "DEV PREVIEW",
) -> Build:
    if env not in ENVS:
        raise ValueError(f"WBOT_ENV must be one of {ENVS}, not {env!r}")
    stats = choose_stats(stats_dir, env)
    banner = {
        "label": dev_label,
        "code": code_commit,
        "stats": "sample" if stats.synthetic else stats_commit,
        "time": (build_time or datetime.now(UTC)).strftime("%Y-%m-%d %H:%M UTC"),
    }
    clean_output(out)
    b = Build(out=out, env=env, stats=stats, jinja=make_jinja(env, stats, banner))
    b.jinja.globals["stop_map"] = copy_map_data(b, tiles_url)

    shutil.copytree(STATIC_DIR, out / "static")
    # Browsers ask for these two at the site root on their own, so they live there too.
    for name in ROOT_ICONS:
        shutil.copyfile(STATIC_DIR / "icons" / name, out / name)
    render_markdown_pages(b)
    render_stats_pages(b)
    b.render("404.html", "404.html", title="Page not found")
    write_sitemap(b)
    check_file_count(out)
    return b


def check_file_count(out: Path) -> None:
    files = sum(1 for p in out.rglob("*") if p.is_file())
    if files > MAX_FILES:
        raise StatsError(f"the build has {files:,} files, over this site's limit of {MAX_FILES:,} "
                         f"(Cloudflare Pages allows {FILE_CAP:,}); see docs/adr/0001-precomputed-page-presets.md")
    log.info("%d files (limit %d)", files, MAX_FILES)


def clean_output(out: Path) -> None:
    """Empties the output directory, but only if it is empty or holds a previous build."""
    if out.exists():
        if any(out.iterdir()) and not (out / "index.html").exists():
            raise SystemExit(f"{out} is not empty and doesn't look like a site build; refusing to delete it")
        shutil.rmtree(out)
    out.mkdir(parents=True)


def copy_map_data(b: Build, tiles_url: str) -> dict | None:
    """Copies the stop map data to /stops/data/; returns the map settings, or None if the stats have none."""
    periods = b.stats.map_periods()
    if not periods:
        return None
    target = b.out / "stops" / "data"
    shutil.copytree(b.stats.map_dir, target / "map")
    shutil.copyfile(b.stats.routes_geojson, target / "routes.geojson")
    stops = [{k: s[k] for k in ("code", "name", "routes", "lat", "lon")} for s in b.stats.index["stops"]]
    text = json.dumps(stops, separators=(",", ":"), ensure_ascii=False)
    (target / "stops.json").write_text(text + "\n", encoding="utf-8", newline="\n")
    return {
        "tiles": tiles_url,
        "routes": [(r["slug"], f"Route {r['short_name']} {r['long_name']}") for r in b.stats.system["routes"]],
        "periods": [(p, "Whole period" if p == "all" else data.month_text(p)) for p in periods],
        "daytypes": list(data.DAYTYPE_LABELS.items()),
        "bands": [(key, filters.band_label(key)) for key, _, _ in contract.BANDS],
        "bins": " ".join(str(b) for b in windows.MAP_BINS),
        "cell": " ".join(contract.MAP_CELL),
        "legend": windows.map_legend(),
    }


def render_markdown_pages(b: Build) -> None:
    for name, path in PAGES.items():
        md = markdown.Markdown(extensions=["meta", "tables"])
        body = md.convert((CONTENT_DIR / f"{name}.md").read_text(encoding="utf-8"))
        for marker, macro in STATS_BLOCKS.items():
            if marker in body:
                body = body.replace(marker, b.jinja.from_string(
                    f'{{% from "macros.html" import {macro} %}}{{{{ {macro}(system) }}}}').render(system=b.stats.system))
        for marker, macro in PATTERN_BLOCKS.items():
            if marker in body:
                body = body.replace(marker, "" if b.stats.patterns is None else b.jinja.from_string(
                    f'{{% from "macros.html" import {macro} %}}{{{{ {macro}(doc) }}}}').render(doc=b.stats.patterns))
        title = md.Meta["title"][0]
        template = "unavailable.html" if name == "unavailable" else "page.html"
        extra = {"csv_files": csv_downloads(b)} if name == "data" else {}
        b.render(template, path, title=title, body=body, **extra)


def csv_downloads(b: Build) -> list[dict]:
    files = []
    target = b.out / "data"
    target.mkdir(parents=True, exist_ok=True)
    for src in b.stats.csv_files:
        shutil.copyfile(src, target / src.name)
        files.append({"name": src.name, "url": f"/data/{src.name}", "kb": max(1, round(src.stat().st_size / 1024))})
    return files


def render_stats_pages(b: Build) -> None:
    stats = b.stats
    notices = data.known_notices(stats.system["notices"], "system.json")
    for daytype, band in page_presets(stats):
        render_home(b, daytype, band, notices)
    for slug, route in stats.routes.items():
        notices = data.known_notices(route["notices"], f"routes/{slug}.json")
        for daytype, band in page_presets(stats, slug):
            render_route(b, slug, daytype, band, notices)
    b.render("stops.html", "stops/", title="Stops", stops=stats.index["stops"], route_names=route_names(stats))
    for code, stop in stats.stops.items():
        b.render(
            "stop.html", f"stops/{code}/", title=f"{data.directions_text(stop['stop']['name'])}, stop {code}", stop=stop, route_names=route_names(stats),
            notices=data.known_notices(stop["notices"], f"stops/{code}.json"),
        )
    b.render("quality.html", "data-quality/", title="Data quality", quality=stats.quality, completeness=stats.system["completeness"])
    if stats.patterns is not None:
        render_patterns(b)


def render_patterns(b: Build) -> None:
    """The Patterns page: every pattern grouped by route in the site's route order, the routes
    with none, then how patterns are found (the rules and thresholds from patterns.json)."""
    stats, doc = b.stats, b.stats.patterns
    names = patterns.Names.from_stats(stats)
    by_route: dict[str, list[dict]] = {}
    for p in sorted(patterns.shown(doc), key=lambda p: p["rank"]):
        by_route.setdefault(p["route"], []).append(
            {"sentence": patterns.sentence(p, names), "detail": patterns.detail(p)})
    too_little = set(doc["too_little_data"])
    routes = [(r, by_route.get(r["slug"], []), r["slug"] in too_little) for r in stats.system["routes"]]
    b.render(
        "patterns.html", PATTERNS_PATH, title="Patterns in late buses", doc=doc,
        with_patterns=[(r, ps) for r, ps, _ in routes if ps],
        without=[(r, little) for r, ps, little in routes if not ps],
        count=sum(len(ps) for ps in by_route.values()),
        pattern_period=data.period_text(doc), ratio=patterns.ratio_words, minutes=patterns.minutes_words,
    )


def page_presets(stats: Stats, slug: str | None = None) -> list[tuple[str, str]]:
    """(daytype, band) of every page to build for the home page (slug None) or a route:
    all-all first, then each preset with a file. Without presets, only all-all."""
    return [(dt, band) for dt in contract.MAP_DAYTYPES for band in contract.MAP_BANDS
            if stats.has_preset(dt, band, slug)]


def page_filter(stats: Stats, base: str, daytype: str, band: str, slug: str | None = None) -> filters.Filter | None:
    """The page's filter and its menus, or None when the stats have no presets."""
    if not stats.presets:
        return None
    return filters.make(base, daytype, band, lambda dt, bd: stats.has_preset(dt, bd, slug))


def render_home(b: Build, daytype: str, band: str, notices: list[dict]) -> None:
    """The home page under one filter. Completeness, trips and notices are never filtered, so
    they come from system.json. Every route keeps its row in the routes table, without a link
    or numbers where it had no service in the preset."""
    stats = b.stats
    flt = page_filter(stats, "/", daytype, band)
    active = bool(flt and flt.active)
    system = {**stats.system, **stats.preset(daytype, band)} if active else stats.system
    rows = {r["slug"]: r for r in system["routes"]}
    route_rows = [
        (base, rows.get(base["slug"]), f"/routes/{base['slug']}/{filters.path(daytype, band)}" if base["slug"] in rows else None)
        for base in stats.system["routes"]
    ]
    b.render(
        "home.html", filters.path(daytype, band), filtered=active, title=flt.title if active else None,
        system=system, route_rows=route_rows, notices=notices, filter=flt,
    )


def render_route(b: Build, slug: str, daytype: str, band: str, notices: list[dict]) -> None:
    stats = b.stats
    route = stats.routes[slug]
    path = f"routes/{slug}/"
    flt = page_filter(stats, "/" + path, daytype, band, slug)
    active = bool(flt and flt.active)
    name = route_name(route["route"])
    if active:
        route = {**route, **stats.preset(daytype, band, slug)}
        title = f"{name}, {flt.words}"
    else:
        title = " ".join(filter(None, [name, route["route"].get("long_name")]))
    b.render(
        "route.html", path + filters.path(daytype, band), filtered=active, title=title,
        name=name, route=route, notices=notices, filter=flt,
    )


def route_name(route: dict) -> str:
    return f"Route {route['short_name']}"


def route_names(stats: Stats) -> dict[str, str]:
    return {r["slug"]: f"{route_name(r)} {r['long_name']}" for r in stats.system["routes"]}


def write_sitemap(b: Build) -> None:
    lastmod = b.stats.meta["generated_at"][:10]
    urls = [p for p in sorted(b.pages) if p not in NO_SITEMAP and p not in b.filtered]
    lines = ['<?xml version="1.0" encoding="UTF-8"?>', '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">']
    lines += [f"<url><loc>{BASE_URL}/{p}</loc><lastmod>{lastmod}</lastmod></url>" for p in urls]
    lines.append("</urlset>")
    (b.out / "sitemap.xml").write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    if b.env == "prod":
        robots = f"User-agent: *\nAllow: /\n\nSitemap: {BASE_URL}/sitemap.xml\n"
    else:
        robots = "User-agent: *\nDisallow: /\n"
    (b.out / "robots.txt").write_text(robots, encoding="utf-8", newline="\n")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="wbot_site.build", description="Build the static site.")
    parser.add_argument("--stats", type=Path, help="stats directory (site/*.json and csv/); dev falls back to the sample")
    parser.add_argument("--out", type=Path, default=Path("dist"), help="output directory (default: dist)")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    env = os.environ.get("WBOT_ENV", "dev")
    try:
        b = build(
            args.stats,
            args.out,
            env,
            code_commit=os.environ.get("WBOT_CODE_COMMIT", "unknown"),
            stats_commit=os.environ.get("WBOT_STATS_COMMIT", "none"),
            tiles_url=os.environ.get("WBOT_TILES_URL", ""),
            dev_label=os.environ.get("WBOT_DEV_LABEL") or "DEV PREVIEW",
        )
    except (StatsError, ValueError) as e:
        log.error("%s", e)
        sys.exit(1)
    log.info("built %d pages (%s, %s data) into %s", len(b.pages), env, "sample" if b.stats.synthetic else "real", args.out)


if __name__ == "__main__":
    main()
