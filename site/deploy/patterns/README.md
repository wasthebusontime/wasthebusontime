# Patterns preview on the dev VM

The `patterns` branch, built from the preview pipeline run's data, served on `http://192.168.1.181:8080/` (home network only). The files here are variants of `../systemd/` and `../Caddyfile` with their own root, `/srv/wbot-patterns`; `tests/test_deploy_variants.py` checks they differ only where they must.

- `rebuild.sh` runs unchanged with `WBOT_DEV_ROOT=/srv/wbot-patterns`, `WBOT_CODE_BRANCH=patterns`, an empty `WBOT_STATS_URL` (no stats repo: the build uses `private-stats/` only, never the published stats) and `WBOT_DEV_LABEL="PATTERNS PREVIEW"` for the banner.
- `patterns.caddy` goes in `/etc/caddy/conf.d/`, which `../Caddyfile` imports.

Install, update and removal, for both VMs: `pipeline/deploy/patterns/README.md`.
