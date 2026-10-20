# Patterns preview

The Patterns feature is built on the `patterns` branch and checked on real data before anything reaches `main`. This folder and `site/deploy/patterns/` set up a second, separate copy of the whole chain:

- **VM 300:** a second checkout on `patterns` (`/opt/wbot/patterns`) runs the pipeline nightly after the main run, from the same archive, into its own derived folder (`/srv/wbot/data/derived-patterns`). It reads the archive only. It **never publishes**: `WBOT_PIPELINE_PREVIEW=1` forces publishing off in code whatever `WBOT_PIPELINE_PUBLISH` says, and its env file has no stats repo, key or healthcheck. After each successful run it copies its site files to VM 301.
- **VM 301:** a second site root (`/srv/wbot-patterns`) built from the `patterns` branch and that copy, served by Caddy on port 8080 to the home network only, with the banner "PATTERNS PREVIEW". The dev site on port 80 is untouched.

Every unit here is a variant of the main one (`../systemd/`, `../devcopy/`, `site/deploy/systemd/`); `tests/test_deploy_variants.py` in each package checks they differ only where they must.

| | Main | Patterns preview |
|---|---|---|
| Code on VM 300 | `/opt/wbot/wasthebusontime` (`main`) | `/opt/wbot/patterns` (`patterns`) |
| Settings | `/etc/wasthebusontime/pipeline.env` | `/etc/wasthebusontime/pipeline-patterns.env` |
| Output | `/srv/wbot/data/derived` | `/srv/wbot/data/derived-patterns` |
| Nightly | `wbot-pipeline.timer`, 04:00 Pacific | `wbot-pipeline-patterns.timer`, 04:10, waits for the main run |
| Copy to VM 301 | `wbot-devcopy` (key `devcopy`) | `wbot-devcopy-patterns` (key `devcopy-patterns`) |
| Site on VM 301 | `/srv/wbot-dev`, `:80`, branch `dev` | `/srv/wbot-patterns`, `:8080`, branch `patterns` |
| Rebuild jobs | `wbot-dev-poll`, `wbot-dev-rebuild` | `wbot-patterns-poll`, `wbot-patterns-rebuild` |

## Install

### VM 300

```bash
# Code and its environment, as wbot.
sudo -u wbot git clone --branch patterns https://github.com/wasthebusontime/wasthebusontime.git /opt/wbot/patterns
cd /opt/wbot/patterns/pipeline && sudo -u wbot -H uv sync --frozen --no-dev
sudo install -d -o wbot -g wbot -m 755 /srv/wbot/data/derived-patterns

# Settings: no secrets in it, root 600 like the main one.
sudo install -m 600 -o root -g root /opt/wbot/patterns/pipeline/deploy/patterns/pipeline-patterns.env.example \
    /etc/wasthebusontime/pipeline-patterns.env

# Its own copy key (VM 301 locks it to the preview's folder, below).
sudo -u wbot -H ssh-keygen -q -t ed25519 -N '' -C 'wbot devcopy patterns' -f /var/lib/wbot/.ssh/devcopy-patterns
sudo cat /var/lib/wbot/.ssh/devcopy-patterns.pub

# Units.
cd /opt/wbot/patterns/pipeline/deploy/patterns
sudo cp wbot-pipeline-patterns.service wbot-pipeline-patterns.timer wbot-devcopy-patterns.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now wbot-pipeline-patterns.timer
```

To update the code: `git pull --ff-only` as wbot in `/opt/wbot/patterns`, then `uv sync --frozen --no-dev` in `pipeline/`. Re-copy the units if they changed. Run by hand (outside the main run's hours, 10:30 to 12:30 UTC) with `sudo systemctl start --no-block wbot-pipeline-patterns`; follow it with `journalctl -fu wbot-pipeline-patterns`.

### VM 301

```bash
# Site root, as the existing wbotdev user; the basemap tiles are shared with :80.
sudo install -d -o wbotdev -g wbotdev -m 755 /srv/wbot-patterns /srv/wbot-patterns/state /srv/wbot-patterns/releases
sudo -u wbotdev ln -s /srv/wbot-dev/tiles /srv/wbot-patterns/tiles
sudo install -d -o devcopy -g devcopy -m 755 /srv/wbot-patterns/private-stats
sudo -u wbotdev git clone --branch patterns https://github.com/wasthebusontime/wasthebusontime.git /srv/wbot-patterns/code
```

Add VM 300's `devcopy-patterns.pub` as a second line in `/var/lib/devcopy/.ssh/authorized_keys`:

```
from="192.168.1.180",restrict,command="/usr/bin/rrsync -wo /srv/wbot-patterns/private-stats" ssh-ed25519 AAAA... wbot devcopy patterns
```

Units, Caddy and firewall:

```bash
cd /srv/wbot-patterns/code/site/deploy
sudo cp patterns/wbot-patterns-poll.service patterns/wbot-patterns-poll.timer patterns/wbot-patterns-rebuild.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now wbot-patterns-poll.timer

# Caddyfile with the conf.d import (the :80 site is unchanged), and the :8080 site.
sudo install -d /etc/caddy/conf.d
sudo install -m 644 patterns/patterns.caddy /etc/caddy/conf.d/patterns.caddy
sudo install -m 644 Caddyfile /etc/caddy/Caddyfile
caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile && sudo systemctl reload caddy
sudo ufw allow from 192.168.1.0/24 to any port 8080 proto tcp comment 'patterns preview'
```

Then copy once from VM 300 (`sudo systemctl start wbot-devcopy-patterns`) and build (`sudo systemctl start wbot-patterns-rebuild`). The last build's log is at `http://192.168.1.181:8080/_status/`.

## Remove

On VM 300:

```bash
sudo systemctl disable --now wbot-pipeline-patterns.timer
sudo rm /etc/systemd/system/wbot-pipeline-patterns.{service,timer} /etc/systemd/system/wbot-devcopy-patterns.service
sudo systemctl daemon-reload
sudo rm /etc/wasthebusontime/pipeline-patterns.env /var/lib/wbot/.ssh/devcopy-patterns /var/lib/wbot/.ssh/devcopy-patterns.pub
sudo rm -rf /opt/wbot/patterns /srv/wbot/data/derived-patterns
```

On VM 301:

```bash
sudo systemctl disable --now wbot-patterns-poll.timer
sudo rm /etc/systemd/system/wbot-patterns-{poll.service,poll.timer,rebuild.service}
sudo systemctl daemon-reload
sudo rm /etc/caddy/conf.d/patterns.caddy && sudo systemctl reload caddy
sudo ufw delete allow from 192.168.1.0/24 to any port 8080 proto tcp
# Delete the "devcopy patterns" line from /var/lib/devcopy/.ssh/authorized_keys.
sudo rm -rf /srv/wbot-patterns
```

The `import` line in the Caddyfile can stay: with no file in `conf.d/` it does nothing.
