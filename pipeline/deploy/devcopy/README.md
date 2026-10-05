# Dev copy (evaluation phase only)

Until publishing is turned on, the stats repo is empty and the dev site would show the sample data. This copies the pipeline's unpublished site files from VM 300 to the dev VM (VM 301) after each successful nightly run, so the dev site shows real numbers on the home network without publishing anything.

- `devcopy.sh` rsyncs `derived/out/site` and `derived/out/csv` to `/srv/wbot-dev/private-stats/` on VM 301.
- `wbot-devcopy.service` runs it as `wbot`. `devcopy.conf` is a drop-in that starts it when `wbot-pipeline.service` succeeds.
- On VM 301, `site/deploy/rebuild.sh` builds from the stats repo if it has data, else from `private-stats/`, else the sample data. A change to `private-stats/` triggers a rebuild within 5 minutes.

The key VM 300 uses can only write into `private-stats/` (rrsync), only from 192.168.1.180, with no shell or forwarding. VM 301 gets no access to VM 300.

**It switches itself off:** with `WBOT_PIPELINE_PUBLISH=1` the script copies nothing, and the dev build prefers the stats repo once that has data. Still remove it when publishing is on (below).

## Install

On VM 301:

```bash
sudo useradd --system --create-home --home-dir /var/lib/devcopy --shell /bin/sh devcopy
sudo install -d -o devcopy -g devcopy -m 755 /srv/wbot-dev/private-stats
sudo install -d -o devcopy -g devcopy -m 700 /var/lib/devcopy/.ssh
```

On VM 300:

```bash
sudo -u wbot -H ssh-keygen -q -t ed25519 -N '' -C 'wbot devcopy' -f /var/lib/wbot/.ssh/devcopy
# Pin VM 301's host key; compare the fingerprint with `ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub` on VM 301.
ssh-keyscan -t ed25519 192.168.1.181 | sudo -u wbot -H tee -a /var/lib/wbot/.ssh/known_hosts
sudo cat /var/lib/wbot/.ssh/devcopy.pub
```

On VM 301, put that public key in `/var/lib/devcopy/.ssh/authorized_keys` (owner devcopy, mode 600) with this prefix:

```
from="192.168.1.180",restrict,command="/usr/bin/rrsync -wo /srv/wbot-dev/private-stats" ssh-ed25519 AAAA... wbot devcopy
```

On VM 300:

```bash
cd /opt/wbot/wasthebusontime/pipeline/deploy/devcopy
sudo cp wbot-devcopy.service /etc/systemd/system/
sudo install -D -m 644 devcopy.conf /etc/systemd/system/wbot-pipeline.service.d/devcopy.conf
sudo systemctl daemon-reload
sudo systemctl start wbot-devcopy      # first copy now; check with journalctl -u wbot-devcopy
```

## Remove (when publishing is turned on)

On VM 300:

```bash
sudo rm /etc/systemd/system/wbot-pipeline.service.d/devcopy.conf /etc/systemd/system/wbot-devcopy.service
sudo rmdir /etc/systemd/system/wbot-pipeline.service.d 2>/dev/null
sudo systemctl daemon-reload
sudo rm /var/lib/wbot/.ssh/devcopy /var/lib/wbot/.ssh/devcopy.pub
```

On VM 301:

```bash
sudo userdel -r devcopy
sudo rm -rf /srv/wbot-dev/private-stats
```

Then delete this folder and the `private-stats` handling in `site/deploy/rebuild.sh`.
