# Site

Builds the static website for Was the Bus On Time? from the published statistics: one page for the whole system, one per route and one per stop, plus the text pages. The build reads precomputed JSON from the stats repo ([wasthebusontime/stats](https://github.com/wasthebusontime/stats)); it never touches raw feed data.

Two runtime dependencies: Jinja2 (templates) and Python-Markdown (text pages). Charts are inline SVG drawn at build time. Pages work without JavaScript. The small site script only remembers the timepoints / all stops choice and runs the stop filter box; the stop map on `/stops/` needs JavaScript, and without it that page is the stop list.

## Layout

```
build.sh                  the build entry point, for the dev VM and Cloudflare Pages
src/wbot_site/build.py    reads the stats, renders every page, writes sitemap.xml and robots.txt
src/wbot_site/data.py     loads and checks schema 1, rounding, "Not enough data", text formats
src/wbot_site/charts.py   the SVG charts, each with a summary sentence and a table
src/wbot_site/templates/  Jinja templates; footer.html is on every page
src/wbot_site/static/     site.css (all colors in variables at the top), site.js, map.js (the stop map)
src/wbot_site/static/vendor/  MapLibre, pmtiles, Protomaps style, fonts and icons (see VENDOR.md)
content/                  Markdown pages: about (including AI use), terms, privacy, methodology, data, unavailable
tools/make_sample.py      writes the synthetic sample stats
tools/make_tiles.sh       cuts the basemap tile file for the stop map (not committed)
tools/make_icons.py       draws the site icon (favicon.svg, favicon.ico, apple-touch-icon.png); run with uv run --with pillow
sample-stats/             the committed sample (invented network, "synthetic": true)
deploy/                   the dev VM rebuild job: script, systemd units, Caddyfile
tests/                    pytest, with chart snapshots in tests/snapshots/
```

The stats directory has the stats repo's layout: `site/meta.json`, `site/system.json`, `site/stops.json`, `site/quality.json`, `site/routes/{slug}.json`, `site/stops/{code}.json`, and `csv/*.csv` (copied to `/data/`). Every JSON file carries `"schema": 1`, and the build refuses a schema number it doesn't know.

## Building locally

Requires [uv](https://docs.astral.sh/uv/). `uv sync` once in this directory.

Linux, macOS, or Git Bash on Windows:

```
cd site
WBOT_STATS_DIR=sample-stats bash build.sh
```

PowerShell on Windows (the same build without the wrapper):

```
cd site
uv run python -m wbot_site.build --stats sample-stats --out dist
```

Then preview at http://localhost:8000/:

```
uv run python -m http.server -d dist 8000
```

Links are absolute (`/routes/901/`), so open the site through a server rather than as files.

### Environment variables

| Variable | Meaning |
|---|---|
| `WBOT_ENV` | `dev` (default) adds the "DEV PREVIEW" banner (code commit, stats commit, build time) and `noindex` to every page, and a `robots.txt` that disallows everything. `prod` has neither. That is the only difference. |
| `WBOT_STATS_DIR` | Stats directory to build from. If unset, `build.sh` clones the public stats repo (`--depth 1`) into a temporary directory. |
| `WBOT_OUT_DIR` | Output directory for `build.sh` (default `dist`). An existing directory is emptied first, but only if it holds a previous build. |
| `WBOT_TILES_URL` | URL of the basemap tile file for the stop map (dev: `/tiles/olympia.pmtiles` on VM 301). If unset, the map shows stops and routes on a plain background. |

### Sample data and the prod guard

- If the stats directory has no `site/meta.json` yet, a **dev** build uses `sample-stats/` instead and every page shows a "SAMPLE DATA" banner.
- A **prod** build fails if the stats are missing, synthetic (`"synthetic": true`), or use an unknown schema, so sample data can't go live by accident.

The sample is an invented network: Routes 901 to 906, stops like "Example St & 1st Ave" with codes E101 and up. It contains no real route names, stop names, codes or schedules. To change it, edit `tools/make_sample.py` and regenerate:

```
uv run python tools/make_sample.py
```

A test fails if `sample-stats/` doesn't match a fresh run.

## Stop map

`/stops/` has a map of every stop, colored by the share of departures on time, with route lines and dropdowns for scope, period (whole period or a month), day type, time of day and route. `/stops/?stop=E101` opens a stop's popup; each stop page links there.

- **Data:** the stats provide `lat` and `lon` in `site/stops.json`, `site/routes.geojson`, and one small file of per-stop counts for each preset, `site/map/{period}/{daytype}-{band}.json`. The build copies them to `/stops/data/`. Stats without them build a site without the map.
- **Basemap:** OpenStreetMap data from Protomaps, as one `.pmtiles` file read with HTTP range requests, so no tile server and no third-party requests. Make it with `bash tools/make_tiles.sh` (needs the [pmtiles CLI](https://github.com/protomaps/go-pmtiles); about 32 MB, a few seconds). It's too big for Pages (25 MiB per file), so prod serves it from Cloudflare R2 under our domain; dev serves it from VM 301.
- **Local preview:** `python -m http.server` can't serve range requests, so a local build shows the map without a basemap unless the tile file is served by something that can (Caddy, for example).
- **Libraries:** vendored and pinned in `src/wbot_site/static/vendor/` (versions and licenses in `VENDOR.md`).

## Tests

```
uv run pytest
```

They check that:

- the sample builds every page;
- every page has the footer (non-affiliation notice, OTD link, licenses, links);
- no internal link is broken;
- dev pages carry the banners and `noindex`;
- prod refuses synthetic, missing or unknown-schema stats;
- two builds are byte-identical.

They also snapshot the charts. After an intended chart change, review and rewrite the snapshots:

```
UPDATE_SNAPSHOTS=1 uv run pytest tests/test_charts.py
git diff tests/snapshots
```

## Cloudflare Pages (prod, not set up yet)

Build command `bash site/build.sh`, output directory `site/dist`, environment `WBOT_ENV=prod`, production branch `main`, preview branches none. The build image has Python and pip but no uv, so `build.sh` installs the pinned uv with pip. Check that step on the first Pages build.

## Dev VM job (VM 301)

The dev preview at http://192.168.1.181/ (home network only) rebuilds itself from the `dev` branch of this repo and the `main` branch of the stats repo.

```
/srv/wbot-dev/code       clone of this repo, branch dev
/srv/wbot-dev/stats      clone of wasthebusontime/stats, branch main
/srv/wbot-dev/releases   one directory per build; current -> the live one
/srv/wbot-dev/state      last tried and last good commits, build log, status page, uv cache
```

- **Polling.** `wbot-dev-poll.timer` runs `wbot-dev-poll.service` every 5 minutes. It runs [deploy/rebuild.sh](deploy/rebuild.sh) as `wbotdev`, which checks both branches with `git ls-remote`.
- **Building.** If the pair of commits hasn't been tried yet, the script:
  1. resets both clones to origin;
  2. runs `build.sh` with `WBOT_ENV=dev` into `releases/<UTC timestamp>/`;
  3. on success, switches `releases/current` atomically and keeps the newest 3 releases.
- **Failures.** The previous site stays up, and the log is at http://192.168.1.181/_status/. The pair is recorded as tried, so the build isn't retried until a new commit arrives.
- **Stats.** Until the stats repo has site output, the dev site shows the sample with the "SAMPLE DATA" banner.
- **Network and secrets.** Both repos are public and read over HTTPS, so the job needs no secrets. It makes two small outbound requests every 5 minutes.

`wbot-dev-rebuild.service` builds immediately, whether or not anything changed. Both services take a lock, so they never run at the same time.

### Installing

Assumes the VM as built in the planning notes: `wbotdev` owns `/srv/wbot-dev`, both repos are cloned there, and Caddy and uv are installed.

```
cd /srv/wbot-dev/code
sudo -u wbotdev git fetch origin dev
sudo -u wbotdev git checkout -B dev origin/dev

# Basemap for the stop map (needs the pmtiles CLI on PATH); optional, the map works without it.
sudo install -d -o wbotdev -g wbotdev /srv/wbot-dev/tiles
sudo -u wbotdev env HOME=/srv/wbot-dev/state bash site/tools/make_tiles.sh /srv/wbot-dev/tiles/olympia.pmtiles

sudo cp site/deploy/systemd/* /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl start wbot-dev-rebuild        # first build; uv fetches Python 3.13 once
cat /srv/wbot-dev/state/build.log

sudo cp site/deploy/Caddyfile /etc/caddy/Caddyfile
sudo systemctl reload caddy
sudo systemctl enable --now wbot-dev-poll.timer
```

### Operating

```
systemctl list-timers 'wbot-dev-*'
journalctl -u wbot-dev-poll -u wbot-dev-rebuild -n 50
sudo systemctl start wbot-dev-rebuild        # force a build now
cat /srv/wbot-dev/state/last_attempt /srv/wbot-dev/state/last_success
```

Adding, replacing or removing the tile file isn't a new commit, so force a build afterwards (`sudo systemctl start wbot-dev-rebuild`). A change to the systemd units or the Caddyfile needs the copy steps above again. A change to `rebuild.sh` takes effect on the next run, because the units run it from the clone.

## Data terms

The transit data behind the statistics is provided "AS IS" by Intercity Transit and used under the [Sound Transit Open Transit Data: Transit Data Terms of Use](https://www.soundtransit.org/help-contacts/business-information/open-transit-data-otd/transit-data-terms-use). This site is unofficial and not affiliated with or endorsed by Intercity Transit.
