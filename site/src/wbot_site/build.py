"""Builds the static site from the published statistics.

    python -m wbot_site.build --stats <stats dir> --out <output dir>

WBOT_ENV=dev (the default) adds the preview banner and noindex; WBOT_ENV=prod has
neither and refuses synthetic or missing stats. In dev, missing stats fall back to
the committed sample with a "SAMPLE DATA" banner.
"""

import argparse
import logging
import os
import shutil
import sys
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import markdown
from jinja2 import Environment, PackageLoader, StrictUndefined

from wbot_site import data
from wbot_site.data import Stats, StatsError

log = logging.getLogger("wbot_site")

SITE_DIR = Path(__file__).resolve().parents[2]
CONTENT_DIR = SITE_DIR / "content"
SAMPLE_DIR = SITE_DIR / "sample-stats"
STATIC_DIR = Path(__file__).resolve().parent / "static"
BASE_URL = "https://wasthebusontime.com"
ENVS = ("dev", "prod")

# Markdown file in content/ -> URL path. Pages listed in NO_SITEMAP carry noindex.
PAGES = {
    "about": "about/",
    "about-ai": "about/ai/",
    "terms": "terms/",
    "privacy": "privacy/",
    "methodology": "methodology/",
    "data": "data/",
    "unavailable": "unavailable/",
}
NO_SITEMAP = {"unavailable/", "404.html"}


@dataclass
class Build:
    out: Path
    env: str
    stats: Stats
    jinja: Environment
    pages: list[str] = field(default_factory=list)

    def render(self, template: str, path: str, **context) -> None:
        """Renders a template to `path` ('' is the home page, 'x/' is x/index.html)."""
        target = self.out / path / "index.html" if path == "" or path.endswith("/") else self.out / path
        target.parent.mkdir(parents=True, exist_ok=True)
        html = self.jinja.get_template(template).render(
            path="/" + path, noindex=self.env == "dev" or path in NO_SITEMAP, **context
        )
        target.write_text(html, encoding="utf-8", newline="\n")
        self.pages.append(path)


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
        meta=stats.meta,
        synthetic=stats.synthetic,
        banner=banner,
        min_sample=stats.meta["min_sample"],
        period=data.period_text(stats.meta),
        scopes=data.SCOPES,
        scope_labels=data.SCOPE_LABELS,
        daytypes=data.DAYTYPES,
        daytype_labels=data.DAYTYPE_LABELS,
        window_labels=data.WINDOW_LABELS,
        enough=data.enough,
        split=data.split,
        percent=data.percent,
        fraction=data.fraction,
    )
    jinja.filters.update(
        number=data.number,
        delay=data.delay_text,
        date=data.date_text,
        month=data.month_text,
        hour=data.hour_text,
        timestamp=data.timestamp_text,
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
) -> Build:
    if env not in ENVS:
        raise ValueError(f"WBOT_ENV must be one of {ENVS}, not {env!r}")
    stats = choose_stats(stats_dir, env)
    banner = {
        "code": code_commit,
        "stats": "sample" if stats.synthetic else stats_commit,
        "time": (build_time or datetime.now(UTC)).strftime("%Y-%m-%d %H:%M UTC"),
    }
    clean_output(out)
    b = Build(out=out, env=env, stats=stats, jinja=make_jinja(env, stats, banner))

    shutil.copytree(STATIC_DIR, out / "static")
    render_markdown_pages(b)
    render_stats_pages(b)
    b.render("404.html", "404.html", title="Page not found")
    write_sitemap(b)
    return b


def clean_output(out: Path) -> None:
    """Empties the output directory, but only if it is empty or holds a previous build."""
    if out.exists():
        if any(out.iterdir()) and not (out / "index.html").exists():
            raise SystemExit(f"{out} is not empty and doesn't look like a site build; refusing to delete it")
        shutil.rmtree(out)
    out.mkdir(parents=True)


def render_markdown_pages(b: Build) -> None:
    for name, path in PAGES.items():
        md = markdown.Markdown(extensions=["meta", "tables"])
        body = md.convert((CONTENT_DIR / f"{name}.md").read_text(encoding="utf-8"))
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
    system = stats.system
    b.render(
        "home.html", "", title="Intercity Transit on-time performance",
        system=system, notices=data.known_notices(system["notices"], "system.json"),
    )
    for slug, route in stats.routes.items():
        name = route_name(route["route"])
        b.render(
            "route.html", f"routes/{slug}/", title=name, name=name, route=route,
            notices=data.known_notices(route["notices"], f"routes/{slug}.json"),
        )
    b.render("stops.html", "stops/", title="Stops", stops=stats.index["stops"], route_names=route_names(stats))
    for code, stop in stats.stops.items():
        b.render(
            "stop.html", f"stops/{code}/", title=stop["stop"]["name"], stop=stop, route_names=route_names(stats),
            notices=data.known_notices(stop["notices"], f"stops/{code}.json"),
        )
    b.render("quality.html", "data-quality/", title="Data quality", quality=stats.quality, completeness=system["completeness"])


def route_name(route: dict) -> str:
    return f"Route {route['short_name']}"


def route_names(stats: Stats) -> dict[str, str]:
    return {r["slug"]: f"{route_name(r)} {r['long_name']}" for r in stats.system["routes"]}


def write_sitemap(b: Build) -> None:
    lastmod = b.stats.meta["generated_at"][:10]
    urls = [p for p in sorted(b.pages) if p not in NO_SITEMAP]
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
        )
    except (StatsError, ValueError) as e:
        log.error("%s", e)
        sys.exit(1)
    log.info("built %d pages (%s, %s data) into %s", len(b.pages), env, "sample" if b.stats.synthetic else "real", args.out)


if __name__ == "__main__":
    main()
