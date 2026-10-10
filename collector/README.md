# Collector

Archives the public GTFS and GTFS-Realtime feeds that Was the Bus On Time? uses. Every response is stored byte for byte as received; nothing is parsed into a database here. The stats pipeline reads these archives later.

## What it does

| Command | Runs | Does |
|---|---|---|
| `wbot-collector run` | always (systemd service) | Fetches TripUpdates and VehiclePositions every 30 s and Alerts every 5 min, aligned to the clock. Writes each response to the spool and one line per attempt to the fetch log. |
| `wbot-collector pack` | hourly | Packs each completed UTC hour of the spool into a `.tar.zst`, re-reads it, checks every file's sha256, then deletes the spool files. |
| `wbot-collector check` | hourly | Warns when free disk space drops below the threshold. |
| `wbot-collector backup` | hourly | `rclone copy` of `archive/`, `static/`, and `log/` to Backblaze B2. Never deletes anything remotely. |
| `wbot-collector static` | daily | Downloads the static GTFS zip and keeps it if its sha256 is new. Every version is kept. |

Polling never goes faster than once every 30 seconds per feed. Requests carry a User-Agent that names the project and a contact address.

## Data layout

Under `WBOT_DATA_DIR`, all times UTC:

```
spool/{feed}/YYYY-MM-DD/HH/{feed}-YYYYMMDDTHHMMSSZ.pb   current hour, raw responses
log/YYYY-MM-DD.jsonl                                     one line per fetch attempt
archive/{feed}/YYYY/MM/DD/{feed}-YYYY-MM-DDTHH.tar.zst   packed and verified hours
static/{feed_version}_{sha256 prefix}.zip                every static GTFS version
static/{feed_version}_{sha256 prefix}.json               when and how it was fetched
state/                                                   small runtime state
```

Feeds are `tripupdates`, `vehiclepositions`, and `alerts`. If `WBOT_WSDOT_ACCESS_CODE` is set, `wsdot_traveltimes` (every 2 minutes) and `wsdot_alerts` (every 5 minutes) are collected too, as unmodified `.json` responses from the WSDOT Traveler Information API, to give I-5 traffic context for routes that use the freeway. The access code is sent as a query parameter and is kept out of the fetch log and error messages. An hour that received files after it was packed gets a second archive named `...THH.1.tar.zst`.

Each fetch log line has `fetched_at`, `feed`, `url`, `status`, `bytes`, `elapsed_ms`, `sha256`, `header_ts` (the feed header timestamp, which this server sets at request time), `data_ts` (newest vehicle or trip update timestamp), `entities`, `path`, and `error`. Failed fetches are logged too, so gaps in the data are documented.

To read an archive:

```
zstd -d -c tripupdates-2026-10-04T02.tar.zst | tar -x
```

## Development

Requires [uv](https://docs.astral.sh/uv/).

```
cd collector
uv sync
uv run pytest
```

Tests use synthetic feeds built in code; no real feed data is stored in this repository.

A short live run, writing to `./data`:

```
uv run wbot-collector run --cycles 6
uv run wbot-collector static
```

## Deploying on Ubuntu Server

Assumes the data disk is mounted at `/srv/wbot/data`.

```
sudo apt install -y git rclone
curl -LsSf https://astral.sh/uv/install.sh | sudo env UV_INSTALL_DIR=/usr/local/bin UV_NO_MODIFY_PATH=1 sh

sudo useradd --system --home-dir /var/lib/wbot --create-home --shell /usr/sbin/nologin wbot
sudo chown wbot:wbot /srv/wbot/data
sudo install -d -o wbot -g wbot /opt/wbot
sudo -u wbot -H git clone https://github.com/wasthebusontime/wasthebusontime.git /opt/wbot/wasthebusontime
cd /opt/wbot/wasthebusontime/collector
sudo -u wbot -H uv sync --frozen --no-dev

sudo install -d -m 700 /etc/wasthebusontime
sudo install -m 600 deploy/collector.env.example /etc/wasthebusontime/collector.env
sudoedit /etc/wasthebusontime/collector.env        # fill in B2 and healthcheck values

sudo cp deploy/systemd/* /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now wbot-collector.service wbot-pack.timer wbot-backup.timer wbot-static.timer
```

Check it:

```
systemctl status wbot-collector
journalctl -u wbot-collector -f
systemctl list-timers 'wbot-*'
ls /srv/wbot/data/spool/tripupdates/*/*
```

Update to the latest code:

```
cd /opt/wbot/wasthebusontime
sudo -u wbot -H git pull
cd collector && sudo -u wbot -H uv sync --frozen --no-dev
sudo cp deploy/systemd/* /etc/systemd/system/ && sudo systemctl daemon-reload
sudo systemctl restart wbot-collector
```

## Configuration

Environment variables, set in `/etc/wasthebusontime/collector.env` (see [deploy/collector.env.example](deploy/collector.env.example)). That file holds secrets: keep it out of the repository and readable by root only.

To start collecting WSDOT data, request a free access code at <https://wsdot.wa.gov/traffic/api/>, set `WBOT_WSDOT_ACCESS_CODE` (and optionally the `WBOT_HC_WSDOT` check), and restart `wbot-collector`. Leave it blank to skip WSDOT.

## Data terms

The transit data is provided "AS IS" by Intercity Transit and used under the [Sound Transit Open Transit Data: Transit Data Terms of Use](https://www.soundtransit.org/help-contacts/business-information/open-transit-data-otd/transit-data-terms-use). Anyone who receives raw data collected by this tool is bound by those terms.
