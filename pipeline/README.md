# Stats pipeline

Turns the collector's archive into the on-time statistics that Was the Bus On Time? publishes. It reads the archived TripUpdates packs, the static GTFS versions and the fetch log, and writes stop events, trip and day facts, aggregates, and the site files (JSON, schema 1) plus CSVs for the public [stats repository](https://github.com/wasthebusontime/stats).

The archive is only ever read. Everything the pipeline writes can be rebuilt from `archive/`, `static/` and `log/`.

## How it works

| Stage | Reads | Writes (under `derived/`) |
|---|---|---|
| Static cache | `static/*.zip` | `static/{version}/*.parquet`, once per GTFS version |
| Stop events, per service date | the date's hourly packs, the GTFS version in effect, the fetch log | `events/YYYY/DATE.parquet`, `trips/YYYY/DATE.parquet`, `days/DATE.json` |
| Aggregates and site files | every processed date | `agg/*.parquet`, `out/site/*.json`, `out/csv/*.csv` |
| Publish (off by default) | `out/` | a commit to the stats repo |

- **Actual times** come from TripUpdates: the feed keeps each passed stop about 15 minutes, with times that stop changing once the bus has left. For each (service date, trip, stop sequence) the pipeline keeps the last times seen. A departure counts as recorded only if the stop was seen after its departure while the trip had a vehicle, and the trip's last update is at or after the departure. Nothing missing is ever guessed.
- **Schedule:** each service date uses the static GTFS version in effect that day: the newest one fetched before the day ended, among those whose dates cover it. Scheduled times are noon minus 12 hours, local time, plus the GTFS time, so DST change days and trips after midnight come out right.
- **Gaps** come from the fetch log, only while trips are scheduled to run. Gaps longer than `gap_tolerance_s` become data-loss windows. `src/wbot_pipeline/gap_causes.toml` records causes and notes for known gaps.
- **Day manifests** (`days/DATE.json`) list every pack read with its sha256, the GTFS version, the pipeline commit and the events version. `meta.json` carries one sha256 over all of them.
- **The on-time rules** (windows, scopes, last stop excluded, thresholds) are applied when the site files are written (the stop events keep every column they need), so a threshold change takes seconds, not a rebuild.

### Which dates run

Each run processes:

- every service date with no outputs yet;
- every date whose packs changed since it was processed (a late `.1` pack, or a restore from backup);
- every date processed by another `EVENTS_VERSION` or `WBOT_PIPELINE_METHODOLOGY_VERSION`;
- the last `trailing_days` dates, regardless.

A date waits until all of its hours are packed. The site files are then rewritten from all processed dates, which takes seconds.

**Full rebuild:** bump `EVENTS_VERSION` in `config.py` when the way stop events are derived changes, or the methodology version in the env file. The next run reprocesses everything. `wbot-pipeline rebuild` does it on demand. It takes about 25 s per service date on the collector VM.

### Settings

Environment variables; see [deploy/pipeline.env.example](deploy/pipeline.env.example). The methodology's provisional thresholds are all settings: `WBOT_PIPELINE_TRIP_COVERAGE_MIN` (0.5), `WBOT_PIPELINE_MIN_SAMPLE` (30), `WBOT_PIPELINE_GAP_TOLERANCE_S` (840), `WBOT_PIPELINE_LOW_COMPLETENESS_BELOW` (0.8), plus `WBOT_PIPELINE_PROVISIONAL` and `WBOT_PIPELINE_METHODOLOGY_VERSION`. The on-time windows (0 to 300 s, and -60 to 300 s) are fixed: the site's labels describe exactly those.

## Development

Requires [uv](https://docs.astral.sh/uv/).

```
cd pipeline
uv sync
uv run pytest
```

Tests build an invented network and simulated feeds in code (`tests/synth.py`), written in the collector's layout. No real feed data is stored in this repository, and nothing derived from it. The end-to-end tests check the output with the site's own loader and build (`wbot-site` is a dev dependency).

### Running on a copy of the real data

Keep the copy outside the repository. For example, from WSL:

```
rsync -a wbot-admin@<collector>:/srv/wbot/data/{archive,static,log} /mnt/c/BusStats-data/
```

Then:

```
cd pipeline
uv run wbot-pipeline --data C:\BusStats-data run       # writes C:\BusStats-data\derived
uv run wbot-pipeline --data C:\BusStats-data status

cd ..\site
uv run python -m wbot_site.build --stats C:\BusStats-data\derived\out --out C:\BusStats-data\dist
```

The last step builds a private preview of the site from the real output. Publishing is off unless `WBOT_PIPELINE_PUBLISH=1`.

| Command | Does |
|---|---|
| `wbot-pipeline run` | the nightly run: process the dates that need it, write site files, publish if enabled |
| `wbot-pipeline rebuild` | reprocess every date, then write and publish |
| `wbot-pipeline day 2026-10-05 ...` | process only these dates, then write and publish |
| `wbot-pipeline site` | rewrite the site files from the stop events already derived |
| `wbot-pipeline publish` | publish the last written site files |
| `wbot-pipeline status` | list dates, their counts, and whether they need processing |

## Deploying on the collector VM

Runs nightly at about 4 am Pacific on the same VM as the collector, at the lowest CPU and disk priority with a 700 MB memory cap, so the collector always wins. A night takes a few minutes. Reading the archive needs about 60 MB, and DuckDB is limited to 256 MB. Assumes the collector is installed per `collector/README.md`.

```
cd /opt/wbot/wasthebusontime
sudo -u wbot -H git pull
cd pipeline && sudo -u wbot -H uv sync --frozen --no-dev

sudo install -d -o wbot -g wbot /srv/wbot/data/derived /var/lib/wbot/stats
sudo install -m 600 deploy/pipeline.env.example /etc/wasthebusontime/pipeline.env
sudoedit /etc/wasthebusontime/pipeline.env          # healthcheck URL; publishing stays 0 for now

sudo cp deploy/systemd/* /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl start wbot-pipeline.service          # first run by hand
journalctl -u wbot-pipeline -n 50
sudo systemctl enable --now wbot-pipeline.timer
```

The collector's hourly backup copies `derived/` (except `tmp/` and `out/`) to B2 next to the archive.

### Publishing to the stats repo

1. Create a deploy key on the VM. Only the public key is printed:
   ```
   sudo -u wbot -H mkdir -p -m 700 /var/lib/wbot/.ssh
   sudo -u wbot -H ssh-keygen -t ed25519 -N '' -C wbot-pipeline -f /var/lib/wbot/.ssh/stats_deploy
   sudo cat /var/lib/wbot/.ssh/stats_deploy.pub
   ```
2. Add it under the stats repo's Settings > Deploy keys, with write access.
3. Pin GitHub's SSH host keys (from GitHub's documentation, "GitHub's SSH key fingerprints") in `/var/lib/wbot/.ssh/known_hosts`, then clone:
   ```
   sudo -u wbot -H env GIT_SSH_COMMAND='ssh -i /var/lib/wbot/.ssh/stats_deploy -o IdentitiesOnly=yes' \
       git clone git@github.com:wasthebusontime/stats.git /var/lib/wbot/stats
   ```
4. Set `WBOT_PIPELINE_PUBLISH=1` in the env file. The next run commits `site/` and `csv/` if anything changed and pushes. Commits use the project's GitHub noreply address.
5. Remove the evaluation-phase dev copy: steps in [deploy/devcopy/README.md](deploy/devcopy/README.md).

### Monitoring

One healthchecks.io check, `pipeline`: cron `0 4 * * *` in `America/Los_Angeles`, grace 2 hours. The run pings `/start` when it begins, then success with a one-line summary, or `/fail` with the error.

## Data terms

The transit data is provided "AS IS" by Intercity Transit and used under the [Sound Transit Open Transit Data: Transit Data Terms of Use](https://www.soundtransit.org/help-contacts/business-information/open-transit-data-otd/transit-data-terms-use). The statistics this pipeline publishes are licensed CC BY 4.0.
